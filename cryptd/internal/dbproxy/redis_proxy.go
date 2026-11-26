package dbproxy

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/epoch"
)

// RedisProxyConfig configures the RESP2/RESP3 cryptographic point-of-contact Redis proxy.
type RedisProxyConfig struct {
	ListenAddr          string
	DefaultUpstream     string
	DefaultServiceID    string
	DialTimeout         time.Duration
	TransparentEncrypt  bool        // When true and service is opted-in, encrypts stored values with SEK_{s,w}
	UpstreamTLS         *tls.Config // Optional TLS config when dialing UpstreamTLS / rediss:// targets
	UpstreamTLSOptional bool        // When true ("auto"), permits self-signed TLS on private/loopback endpoints if no ExplicitCA
	ExplicitCA          bool        // True when an explicit upstream CA file was configured
}

// RedisProxy gates Redis TCP connections at the point of contact, enforces lease/epoch lock
// status, severs open client sockets on lock, and transparently encrypts/decrypts values
// using per-service SEK_{service,week} keys so Redis RAM/RDB/AOF store ciphertext only.
type RedisProxy struct {
	cfg      RedisProxyConfig
	epochs   *epoch.Manager
	router   *Router
	listener net.Listener
}

// NewRedisProxy constructs a Redis cryptographic proxy.
func NewRedisProxy(cfg RedisProxyConfig, epochs *epoch.Manager, router *Router) (*RedisProxy, error) {
	if cfg.DialTimeout <= 0 {
		cfg.DialTimeout = 10 * time.Second
	}
	return &RedisProxy{
		cfg:    cfg,
		epochs: epochs,
		router: router,
	}, nil
}

// ListenAndServe opens the TCP listener and serves Redis connections until ctx cancels.
func (p *RedisProxy) ListenAndServe(ctx context.Context) error {
	ln, err := net.Listen("tcp", p.cfg.ListenAddr)
	if err != nil {
		return err
	}
	p.listener = ln
	return p.Serve(ctx, ln)
}

// Serve accepts Redis client connections on an existing listener.
func (p *RedisProxy) Serve(ctx context.Context, ln net.Listener) error {
	p.listener = ln
	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()
	for {
		conn, err := ln.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return nil
			default:
				return err
			}
		}
		go p.handleConn(ctx, conn)
	}
}

type decryptShape uint8

const (
	shapeNone decryptShape = iota
	shapeSingleBulk
	shapeArrayAllBulk
	shapeArrayOddValues // HGETALL: [field0, val0, field1, val1, ...]
)

type pendingCmd struct {
	serviceID     string
	projectID     string
	optedIn       bool
	shape         decryptShape
	pubsub        bool
	authDone      chan bool
	execShapes    []pendingCmd
	localReply    *respValue
	overrideReply *respValue
}

type respAttrBlock struct {
	pairs []respValue // 2 * N items
}

type respValue struct {
	kind  byte // '+', '-', ':', '$', '*', '_', ',', '#', '(', '!', '=', '%', '~', '>'
	str   []byte
	isNil bool
	arr   []respValue
	attrs []respAttrBlock
}

type mutableConnPair struct {
	mu       sync.Mutex
	client   net.Conn
	upstream net.Conn
	closed   bool
}

func resolveRedisDialTarget(raw string, explicitTLS bool) (dialAddr string, serverName string, useTLS bool) {
	s := strings.TrimSpace(raw)
	useTLS = explicitTLS
	lower := strings.ToLower(s)
	if strings.HasPrefix(lower, "rediss://") {
		useTLS = true
	}
	if strings.Contains(s, "://") {
		if u, err := url.Parse(s); err == nil && u.Host != "" {
			s = u.Host
			if u.Port() == "" && u.Hostname() != "" {
				s = net.JoinHostPort(u.Hostname(), "6379")
			}
		}
	}
	if host, _, err := net.SplitHostPort(s); err == nil && host != "" {
		if net.ParseIP(host) == nil {
			serverName = host
		}
	}
	return s, serverName, useTLS
}

func (m *mutableConnPair) SetUpstream(u net.Conn) {
	m.mu.Lock()
	alreadyClosed := m.closed
	m.upstream = u
	m.mu.Unlock()
	if alreadyClosed && u != nil {
		_ = u.Close()
	}
}

func (m *mutableConnPair) Close() error {
	m.mu.Lock()
	m.closed = true
	c := m.client
	u := m.upstream
	m.mu.Unlock()
	if c != nil {
		_ = c.Close()
	}
	if u != nil {
		_ = u.Close()
	}
	return nil
}

func (p *RedisProxy) handleConn(ctx context.Context, client net.Conn) {
	defer client.Close()

	initialService := p.cfg.DefaultServiceID
	if initialService == "" {
		initialService = crypto.ControlPlaneServiceID
	}
	initialTarget, _ := p.router.LookupService(initialService)

	// Gate immediately on global epoch lock before serving commands.
	if st := p.epochs.Status(); st.State == epoch.StateLocked {
		_, _ = io.WriteString(client, "-ERR LOCKED trulay-grid encryption controller is locked\r\n")
		return
	}

	pair := &mutableConnPair{client: client}
	boundService := initialService
	p.router.TrackConn(boundService, pair)
	var bindMu sync.Mutex
	defer func() {
		bindMu.Lock()
		s := boundService
		bindMu.Unlock()
		p.router.UntrackConn(s, pair)
		_ = pair.Close()
	}()

	cReader := bufio.NewReaderSize(client, 64*1024)
	cWriter := bufio.NewWriterSize(client, 64*1024)
	var cWriteMu sync.Mutex
	writeClientError := func(msg string) {
		cWriteMu.Lock()
		_, _ = fmt.Fprintf(cWriter, "-ERR %s\r\n", msg)
		_ = cWriter.Flush()
		cWriteMu.Unlock()
	}

	var (
		upstream     net.Conn
		upstreamAddr string
		upstreamTLS  bool
		uReader      *bufio.Reader
		uWriter      *bufio.Writer
	)

	type uRespMsg struct {
		val respValue
		err error
	}

	pendingCh := make(chan pendingCmd, 1024)
	uRespCh := make(chan uRespMsg)
	upstreamClosed := make(chan struct{})
	errCh := make(chan error, 2)

	var (
		inflightMu sync.Mutex
		inflight   int
		drainedCh  chan struct{}
	)
	incInflight := func() {
		inflightMu.Lock()
		inflight++
		inflightMu.Unlock()
	}
	decInflight := func() {
		inflightMu.Lock()
		if inflight > 0 {
			inflight--
		}
		if inflight == 0 && drainedCh != nil {
			close(drainedCh)
			drainedCh = nil
		}
		inflightMu.Unlock()
	}
	waitInflight := func() {
		inflightMu.Lock()
		if inflight == 0 {
			inflightMu.Unlock()
			return
		}
		ch := drainedCh
		if ch == nil {
			ch = make(chan struct{})
			drainedCh = ch
		}
		inflightMu.Unlock()
		select {
		case <-ch:
		case <-upstreamClosed:
		case <-ctx.Done():
		}
	}
	flushAndWaitInflight := func() error {
		if uWriter != nil && uWriter.Buffered() > 0 {
			if ferr := uWriter.Flush(); ferr != nil {
				return ferr
			}
		}
		waitInflight()
		return nil
	}

	startUpstream := func(targetAddr string, targetTLS bool) error {
		rawAddr := targetAddr
		wantTLS := targetTLS
		if rawAddr == "" {
			rawAddr = initialTarget.UpstreamRedis
			wantTLS = wantTLS || initialTarget.UpstreamTLS
		}
		if rawAddr == "" {
			rawAddr = p.cfg.DefaultUpstream
		}
		if rawAddr == "" {
			return errors.New("upstream Redis address not configured")
		}
		dialAddr, serverName, useTLS := resolveRedisDialTarget(rawAddr, wantTLS)
		if upstream != nil {
			if dialAddr != upstreamAddr || useTLS != upstreamTLS {
				return errors.New("CROSS_UPSTREAM_REAUTH reconnect and authenticate immediately when targeting dedicated upstream")
			}
			return nil
		}
		dialer := &net.Dialer{Timeout: p.cfg.DialTimeout}
		var (
			conn net.Conn
			derr error
		)
		if useTLS {
			var tlsCfg *tls.Config
			if p.cfg.UpstreamTLS != nil {
				tlsCfg = p.cfg.UpstreamTLS.Clone()
			} else {
				tlsCfg = &tls.Config{
					MinVersion: tls.VersionTLS12,
				}
			}
			if p.cfg.UpstreamTLSOptional && !p.cfg.ExplicitCA && isLoopbackOrPrivateEndpoint(dialAddr) {
				tlsCfg.InsecureSkipVerify = true // #nosec G402 -- internal docker/loopback Redis TLS without public IP SAN
			}
			if tlsCfg.ServerName == "" && serverName != "" {
				tlsCfg.ServerName = serverName
			}
			conn, derr = tls.DialWithDialer(dialer, "tcp", dialAddr, tlsCfg)
		} else {
			conn, derr = dialer.DialContext(ctx, "tcp", dialAddr)
		}
		if derr != nil {
			return fmt.Errorf("upstream Redis dial failed: %w", derr)
		}
		upstream = conn
		upstreamAddr = dialAddr
		upstreamTLS = useTLS
		pair.SetUpstream(conn)
		uReader = bufio.NewReaderSize(conn, 64*1024)
		uWriter = bufio.NewWriterSize(conn, 64*1024)

		// Reader goroutine: owns uReader exclusively and feeds uRespCh.
		go func() {
			for {
				val, rerr := readRESP(uReader)
				select {
				case uRespCh <- uRespMsg{val: val, err: rerr}:
				case <-upstreamClosed:
					return
				}
				if rerr != nil {
					return
				}
			}
		}()

		// Ordered reply loop: processes local errors and upstream replies in strict FIFO order.
		go func() {
			defer close(upstreamClosed)
			inPubSub := false

			writeLocal := func(meta pendingCmd) error {
				cWriteMu.Lock()
				werr := writeRESP(cWriter, meta.localReply)
				if werr == nil && len(pendingCh) == 0 {
					werr = cWriter.Flush()
				}
				cWriteMu.Unlock()
				decInflight()
				return werr
			}

			writePush := func(val *respValue) error {
				cWriteMu.Lock()
				werr := writeRESP(cWriter, val)
				if werr == nil {
					werr = cWriter.Flush()
				}
				cWriteMu.Unlock()
				return werr
			}

			processMatchedReply := func(meta pendingCmd, val respValue) error {
				if meta.authDone != nil {
					ok := val.kind != '-' && val.kind != '!'
					meta.authDone <- ok
				}
				if meta.pubsub {
					inPubSub = true
				}
				if meta.overrideReply != nil {
					val = *meta.overrideReply
				} else if p.cfg.TransparentEncrypt && (len(meta.execShapes) > 0 || (meta.optedIn && meta.shape != shapeNone)) {
					if derr := p.decryptReply(&val, meta); derr != nil {
						val = respValue{kind: '-', str: []byte("ERR LOCKED trulay-grid decryption failed: " + derr.Error())}
					}
				}
				cWriteMu.Lock()
				werr := writeRESP(cWriter, &val)
				if werr == nil && (inPubSub || len(pendingCh) == 0) {
					werr = cWriter.Flush()
				}
				cWriteMu.Unlock()
				decInflight()
				return werr
			}

			for {
				if inPubSub {
					select {
					case msg := <-uRespCh:
						if msg.err != nil {
							errCh <- msg.err
							return
						}
						if werr := writePush(&msg.val); werr != nil {
							errCh <- werr
							return
						}
					case <-ctx.Done():
						return
					}
					continue
				}

				var meta pendingCmd
				haveMeta := false
				select {
				case meta = <-pendingCh:
					haveMeta = true
				default:
					select {
					case meta = <-pendingCh:
						haveMeta = true
					case msg := <-uRespCh:
						if msg.err != nil {
							errCh <- msg.err
							return
						}
						if msg.val.kind == '>' {
							if werr := writePush(&msg.val); werr != nil {
								errCh <- werr
								return
							}
							continue
						}
						// Normal upstream reply arrived concurrently with pendingCh:
						// drain preceding localReply entries and match the FIFO pendingCmd.
						for {
							select {
							case m := <-pendingCh:
								if m.localReply != nil {
									if werr := writeLocal(m); werr != nil {
										errCh <- werr
										return
									}
									continue
								}
								if werr := processMatchedReply(m, msg.val); werr != nil {
									errCh <- werr
									return
								}
							case <-ctx.Done():
								return
							}
							break
						}
						continue
					case <-ctx.Done():
						return
					}
				}

				if !haveMeta {
					continue
				}
				if meta.localReply != nil {
					if werr := writeLocal(meta); werr != nil {
						errCh <- werr
						return
					}
					continue
				}

				var msg uRespMsg
				for {
					select {
					case msg = <-uRespCh:
					case <-ctx.Done():
						return
					}
					if msg.err != nil {
						errCh <- msg.err
						return
					}
					if msg.val.kind == '>' {
						// RESP3 out-of-band push frame: forward without consuming pendingCmd.
						if werr := writePush(&msg.val); werr != nil {
							errCh <- werr
							return
						}
						continue
					}
					break
				}

				if werr := processMatchedReply(meta, msg.val); werr != nil {
					errCh <- werr
					return
				}
			}
		}()
		return nil
	}

	// Client -> Upstream request loop
	go func() {
		var aclTarget *ServiceTarget
		var stickyTarget *ServiceTarget
		var inMulti bool
		var multiDirty bool
		var multiQueue []pendingCmd
		for {
			args, inline, rerr := readRESPCommand(cReader)
			if rerr != nil {
				errCh <- rerr
				return
			}
			_ = inline
			if len(args) == 0 {
				continue
			}

			cmd := strings.ToUpper(string(args[0]))

			if cmd == "RESET" {
				if upstream == nil {
					writeClientError("RESET is not supported on cryptographic proxy")
					continue
				}
				if uWriter.Buffered() > 0 {
					if ferr := uWriter.Flush(); ferr != nil {
						errCh <- ferr
						return
					}
				}
				incInflight()
				select {
				case pendingCh <- pendingCmd{
					localReply: &respValue{kind: '-', str: []byte("ERR RESET is not supported on cryptographic proxy")},
				}:
				case <-upstreamClosed:
					decInflight()
					return
				case <-ctx.Done():
					decInflight()
					return
				}
				continue
			}

			// Determine auth candidate WITHOUT activating until upstream validates credentials.
			var authCandidate *ServiceTarget
			hasAuthArg := false
			if cmd == "AUTH" && len(args) >= 3 {
				hasAuthArg = true
				if tgt, ok := p.router.ResolveRedisUser(string(args[1])); ok {
					authCandidate = &tgt
				}
			} else if cmd == "AUTH" && len(args) == 2 {
				hasAuthArg = true
				if tgt, ok := p.router.ResolveRedisUser("default"); ok {
					authCandidate = &tgt
				}
			} else if cmd == "HELLO" {
				for i := 1; i+2 < len(args); i++ {
					if strings.EqualFold(string(args[i]), "AUTH") {
						hasAuthArg = true
						if tgt, ok := p.router.ResolveRedisUser(string(args[i+1])); ok {
							authCandidate = &tgt
						}
						break
					}
				}
			}

			// Determine per-command effective service target (ACL user > Connection default; unauthenticated prefix hopping rejected).
			effTarget := initialTarget
			if stickyTarget != nil {
				effTarget = *stickyTarget
			}
			if authCandidate != nil {
				effTarget = *authCandidate
			} else if aclTarget != nil {
				effTarget = *aclTarget
			}
			if authCandidate == nil && len(args) >= 2 && keyBearingCommand(cmd) {
				if resolved, multiErr := p.resolveMultiKeyTarget(cmd, args, effTarget); multiErr != nil {
					if ferr := flushAndWaitInflight(); ferr != nil {
						errCh <- ferr
						return
					}
					writeClientError(multiErr.Error())
					errCh <- multiErr
					return
				} else if resolved.ServiceID != effTarget.ServiceID {
					if aclTarget != nil {
						hopErr := errors.New("CROSS_SERVICE_ACCESS authenticated connection cannot access another service key prefix")
						if ferr := flushAndWaitInflight(); ferr != nil {
							errCh <- ferr
							return
						}
						writeClientError(hopErr.Error())
						errCh <- hopErr
						return
					}
					if stickyTarget != nil {
						hopErr := errors.New("AUTHENTICATION_REQUIRED cross-service key prefix access requires AUTH")
						if ferr := flushAndWaitInflight(); ferr != nil {
							errCh <- ferr
							return
						}
						writeClientError(hopErr.Error())
						errCh <- hopErr
						return
					}
					copySticky := resolved
					stickyTarget = &copySticky
					effTarget = resolved
					bindMu.Lock()
					if boundService != resolved.ServiceID {
						p.router.RebindConn(boundService, resolved.ServiceID, pair)
						boundService = resolved.ServiceID
					}
					bindMu.Unlock()
				}
			}

			optedIn, projectID, authErr := p.epochs.AuthorizeDBAccess(effTarget.ServiceID)
			if authErr != nil {
				if ferr := flushAndWaitInflight(); ferr != nil {
					errCh <- ferr
					return
				}
				writeClientError(fmt.Sprintf("LOCKED trulay-grid encryption controller blocked Redis access: %v", authErr))
				errCh <- authErr
				return
			}

			if uerr := startUpstream(effTarget.UpstreamRedis, effTarget.UpstreamTLS); uerr != nil {
				writeClientError(uerr.Error())
				errCh <- uerr
				return
			}

			encryptActive := p.cfg.TransparentEncrypt && optedIn && effTarget.EncryptRedisVals
			if encryptActive && isUnsupportedEncryptedRedisCmd(cmd) {
				if inMulti {
					multiDirty = true
				}
				if uWriter.Buffered() > 0 {
					if ferr := uWriter.Flush(); ferr != nil {
						errCh <- ferr
						return
					}
				}
				errMsg := fmt.Sprintf("ERR ENCRYPTED_KEY_UNSUPPORTED %s cannot operate on transparently encrypted values", cmd)
				incInflight()
				select {
				case pendingCh <- pendingCmd{
					localReply: &respValue{kind: '-', str: []byte(errMsg)},
				}:
				case <-upstreamClosed:
					decInflight()
					return
				case <-ctx.Done():
					decInflight()
					return
				}
				continue
			}
			if err := p.encryptCommandArgs(cmd, args, encryptActive, effTarget.ServiceID, projectID); err != nil {
				if ferr := flushAndWaitInflight(); ferr != nil {
					errCh <- ferr
					return
				}
				writeClientError(fmt.Sprintf("LOCKED encryption unavailable: %v", err))
				errCh <- err
				return
			}

			shape := expectedDecryptShape(cmd, args)
			var execShapes []pendingCmd
			var overrideReply *respValue
			if cmd == "MULTI" {
				if !inMulti {
					inMulti = true
					multiDirty = false
					multiQueue = nil
				}
				shape = shapeNone
			} else if cmd == "DISCARD" {
				inMulti = false
				multiDirty = false
				multiQueue = nil
				shape = shapeNone
			} else if cmd == "EXEC" {
				if inMulti && multiDirty {
					args = [][]byte{[]byte("DISCARD")}
					overrideReply = &respValue{kind: '-', str: []byte("EXECABORT Transaction discarded because of previous errors.")}
				} else {
					execShapes = multiQueue
				}
				inMulti = false
				multiDirty = false
				multiQueue = nil
				shape = shapeNone
			} else if inMulti && cmd != "AUTH" && cmd != "HELLO" && cmd != "QUIT" {
				multiQueue = append(multiQueue, pendingCmd{
					serviceID: effTarget.ServiceID,
					projectID: projectID,
					optedIn:   optedIn,
					shape:     shape,
				})
				shape = shapeNone
			}
			isPubSub := cmd == "SUBSCRIBE" || cmd == "PSUBSCRIBE" || cmd == "SSUBSCRIBE" || cmd == "MONITOR"
			var authDoneCh chan bool
			isAuthCmd := cmd == "AUTH" || hasAuthArg
			if isAuthCmd {
				authDoneCh = make(chan bool, 1)
			}

			incInflight()
			select {
			case pendingCh <- pendingCmd{
				serviceID:     effTarget.ServiceID,
				projectID:     projectID,
				optedIn:       optedIn,
				shape:         shape,
				pubsub:        isPubSub,
				authDone:      authDoneCh,
				execShapes:    execShapes,
				overrideReply: overrideReply,
			}:
			case <-upstreamClosed:
				decInflight()
				return
			case <-ctx.Done():
				decInflight()
				return
			}

			if werr := writeRESPArrayArgs(uWriter, args); werr != nil {
				errCh <- werr
				return
			}
			if authDoneCh != nil || cReader.Buffered() == 0 || len(pendingCh) >= 512 {
				if ferr := uWriter.Flush(); ferr != nil {
					errCh <- ferr
					return
				}
			}
			if authDoneCh != nil {
				select {
				case authOK := <-authDoneCh:
					if authOK && authCandidate != nil {
						aclTarget = authCandidate
						copySticky := *authCandidate
						stickyTarget = &copySticky
						bindMu.Lock()
						p.router.RebindConn(boundService, authCandidate.ServiceID, pair)
						boundService = authCandidate.ServiceID
						bindMu.Unlock()
					} else if !authOK && isAuthCmd {
						// Fail closed: never clear stickyTarget or aclTarget on failed AUTH
						// so a bound connection cannot hop to another tenant prefix.
						if stickyTarget != nil || aclTarget != nil {
							waitInflight()
							errCh <- errors.New("AUTHENTICATION_FAILED failed AUTH on bound connection")
							return
						}
					}
				case <-upstreamClosed:
					return
				}
			}
			if isPubSub {
				_, cerr := io.Copy(uWriter, cReader)
				_ = uWriter.Flush()
				errCh <- cerr
				return
			}
		}
	}()

	select {
	case <-errCh:
	case <-ctx.Done():
	}
}

func (p *RedisProxy) resolveMultiKeyTarget(cmd string, args [][]byte, fallback ServiceTarget) (ServiceTarget, error) {
	var keyIndices []int
	switch cmd {
	case "MGET", "DEL", "UNLINK", "EXISTS", "SDIFF", "SINTER", "SUNION", "PFMERGE":
		for i := 1; i < len(args); i++ {
			keyIndices = append(keyIndices, i)
		}
	case "BLPOP", "BRPOP":
		for i := 1; i+1 < len(args); i++ {
			keyIndices = append(keyIndices, i)
		}
	case "RENAME", "RENAMENX", "COPY", "RPOPLPUSH", "BRPOPLPUSH", "LMOVE", "BLMOVE", "SMOVE":
		keyIndices = []int{1, 2}
	case "MSET", "MSETNX":
		for i := 1; i < len(args); i += 2 {
			keyIndices = append(keyIndices, i)
		}
	default:
		keyIndices = []int{1}
	}

	var matched *ServiceTarget
	for _, idx := range keyIndices {
		if idx >= len(args) {
			continue
		}
		if tgt, ok := p.router.ResolveRedisKey(string(args[idx])); ok {
			if matched == nil {
				copyTgt := tgt
				matched = &copyTgt
			} else if matched.ServiceID != tgt.ServiceID {
				return fallback, errors.New("cross-service multi-key command rejected")
			}
		} else if matched != nil {
			return fallback, errors.New("cross-service multi-key command rejected")
		}
	}
	if matched != nil {
		return *matched, nil
	}
	return fallback, nil
}

func keyBearingCommand(cmd string) bool {
	switch cmd {
	case "GET", "SET", "SETEX", "PSETEX", "SETNX", "GETSET", "GETDEL", "GETEX",
		"MGET", "MSET", "MSETNX", "PUBLISH",
		"APPEND", "SETRANGE", "GETRANGE", "SUBSTR",
		"SETBIT", "GETBIT", "BITCOUNT", "BITPOS", "BITOP", "BITFIELD", "BITFIELD_RO",
		"INCR", "DECR", "INCRBY", "DECRBY", "INCRBYFLOAT", "STRLEN",
		"DEL", "UNLINK", "EXISTS", "EXPIRE", "PEXPIRE", "EXPIREAT", "PEXPIREAT", "PERSIST", "TTL", "PTTL",
		"RENAME", "RENAMENX", "COPY", "TYPE", "DUMP", "RESTORE",
		"HSET", "HGET", "HMSET", "HMGET", "HGETALL", "HVALS", "HKEYS", "HLEN", "HEXISTS", "HSETNX", "HDEL",
		"LPUSH", "RPUSH", "LPUSHX", "RPUSHX", "LPOP", "RPOP", "BLPOP", "BRPOP",
		"LRANGE", "LINDEX", "LSET", "LLEN", "LTRIM", "LREM", "RPOPLPUSH", "BRPOPLPUSH", "LMOVE", "BLMOVE",
		"SADD", "SREM", "SMEMBERS", "SCARD", "SISMEMBER", "SMOVE", "SDIFF", "SINTER", "SUNION",
		"ZADD", "ZREM", "ZRANGE", "ZCARD", "ZSCORE",
		"PFADD", "PFCOUNT", "PFMERGE":
		return true
	}
	return false
}

func isUnsupportedEncryptedRedisCmd(cmd string) bool {
	switch cmd {
	case "APPEND", "SETRANGE", "GETRANGE", "SUBSTR",
		"SETBIT", "GETBIT", "BITCOUNT", "BITPOS", "BITOP",
		"BITFIELD", "BITFIELD_RO":
		return true
	}
	return false
}

func expectedDecryptShape(cmd string, args [][]byte) decryptShape {
	switch cmd {
	case "GET", "GETSET", "GETDEL", "GETEX", "HGET", "LINDEX", "RPOPLPUSH", "BRPOPLPUSH", "LMOVE", "BLMOVE":
		return shapeSingleBulk
	case "SET":
		for i := 3; i < len(args); i++ {
			if strings.EqualFold(string(args[i]), "GET") {
				return shapeSingleBulk
			}
		}
	case "MGET", "HMGET", "LRANGE", "HVALS":
		return shapeArrayAllBulk
	case "HGETALL", "BLPOP", "BRPOP":
		return shapeArrayOddValues
	case "LPOP", "RPOP":
		// LPOP key returns bulk; LPOP key count returns array -> handle both cleanly.
		return shapeArrayAllBulk
	}
	return shapeNone
}

func (p *RedisProxy) encryptCommandArgs(cmd string, args [][]byte, active bool, serviceID, projectID string) error {
	if !active {
		return nil
	}
	var valIndices []int
	switch cmd {
	case "SET", "SETNX", "GETSET", "PUBLISH":
		if len(args) >= 3 {
			valIndices = []int{2}
		}
	case "SETEX", "PSETEX", "LSET":
		if len(args) >= 4 {
			valIndices = []int{3}
		}
	case "MSET", "MSETNX":
		for i := 2; i < len(args); i += 2 {
			valIndices = append(valIndices, i)
		}
	case "HSET", "HMSET":
		for i := 3; i < len(args); i += 2 {
			valIndices = append(valIndices, i)
		}
	case "HSETNX":
		if len(args) >= 4 {
			valIndices = []int{3}
		}
	case "LPUSH", "RPUSH", "LPUSHX", "RPUSHX":
		for i := 2; i < len(args); i++ {
			valIndices = append(valIndices, i)
		}
	default:
		return nil
	}
	if len(valIndices) == 0 {
		return nil
	}

	dek, _, _, err := p.epochs.EnsureServiceDEK(serviceID, projectID)
	if err != nil {
		return err
	}
	defer crypto.ZeroBytes(dek)
	redisKEK, err := crypto.DerivePurposeSubkey(dek, "redis-persistent")
	if err != nil {
		return err
	}
	defer crypto.ZeroBytes(redisKEK)

	for _, idx := range valIndices {
		enc, err := crypto.EncryptRedisValue(redisKEK, 0, serviceID, args[idx])
		if err != nil {
			return err
		}
		args[idx] = enc
	}
	return nil
}

func (p *RedisProxy) decryptReply(val *respValue, meta pendingCmd) error {
	if len(meta.execShapes) > 0 && val.kind == '*' && !val.isNil {
		for i := range val.arr {
			if i < len(meta.execShapes) {
				sub := meta.execShapes[i]
				if sub.optedIn && sub.shape != shapeNone {
					if err := p.decryptReply(&val.arr[i], sub); err != nil {
						val.arr[i] = respValue{kind: '-', str: []byte("ERR LOCKED trulay-grid decryption failed: " + err.Error())}
					}
				}
			}
		}
		return nil
	}
	switch meta.shape {
	case shapeSingleBulk:
		if val.kind == '$' && !val.isNil {
			pt, err := p.maybeDecryptRedisBytes(val.str, meta.serviceID, meta.projectID)
			if err != nil {
				return err
			}
			val.str = pt
		}
	case shapeArrayAllBulk:
		if val.kind == '$' && !val.isNil {
			// Handles LPOP/RPOP single-arg reply cleanly as well as array LPOP count.
			pt, err := p.maybeDecryptRedisBytes(val.str, meta.serviceID, meta.projectID)
			if err != nil {
				return err
			}
			val.str = pt
			return nil
		}
		if val.kind == '*' && !val.isNil {
			for i := range val.arr {
				if val.arr[i].kind == '$' && !val.arr[i].isNil {
					pt, err := p.maybeDecryptRedisBytes(val.arr[i].str, meta.serviceID, meta.projectID)
					if err != nil {
						return err
					}
					val.arr[i].str = pt
				}
			}
		}
	case shapeArrayOddValues:
		if (val.kind == '*' || val.kind == '%') && !val.isNil {
			for i := 1; i < len(val.arr); i += 2 {
				if val.arr[i].kind == '$' && !val.arr[i].isNil {
					pt, err := p.maybeDecryptRedisBytes(val.arr[i].str, meta.serviceID, meta.projectID)
					if err != nil {
						return err
					}
					val.arr[i].str = pt
				}
			}
		}
	}
	return nil
}

func (p *RedisProxy) maybeDecryptRedisBytes(raw []byte, serviceID, projectID string) ([]byte, error) {
	if !bytes.HasPrefix(raw, []byte(crypto.RedisValuePrefix)) {
		return raw, nil
	}
	week, blob, err := crypto.ParseRedisValueWeek(raw)
	if err != nil {
		return nil, err
	}
	if week == 0 {
		dek, _, _, derr := p.epochs.EnsureServiceDEK(serviceID, projectID)
		if derr != nil {
			return nil, derr
		}
		defer crypto.ZeroBytes(dek)
		redisKEK, kerr := crypto.DerivePurposeSubkey(dek, "redis-persistent")
		if kerr != nil {
			return nil, kerr
		}
		defer crypto.ZeroBytes(redisKEK)
		return crypto.DecryptRedisValue(redisKEK, 0, serviceID, blob)
	}
	sek, err := p.epochs.SEKForWeekOrFetch(projectID, serviceID, week)
	if err != nil {
		return nil, err
	}
	defer crypto.ZeroBytes(sek)
	return crypto.DecryptRedisValue(sek, week, serviceID, blob)
}

func readRESPCommand(r *bufio.Reader) ([][]byte, bool, error) {
	for {
		b, err := r.Peek(1)
		if err != nil {
			return nil, false, err
		}
		if b[0] == '\r' || b[0] == '\n' {
			_, _ = r.ReadByte()
			continue
		}
		if b[0] == '*' {
			lim := respLimits{
				maxElements: maxRESPElements,
				maxBytes:    maxRESPCommandBytes,
			}
			v, err := readRESPDepth(r, 0, &lim)
			if err != nil {
				return nil, false, err
			}
			if len(v.arr) > maxRESPElements {
				return nil, false, fmt.Errorf("RESP array count %d exceeds %d limit", len(v.arr), maxRESPElements)
			}
			args := make([][]byte, 0, len(v.arr))
			for _, elem := range v.arr {
				if elem.isNil {
					args = append(args, nil)
				} else {
					args = append(args, elem.str)
				}
			}
			return args, false, nil
		}
		line, err := readRESPLine(r)
		if err != nil {
			return nil, true, err
		}
		fields := strings.Fields(string(line))
		if len(fields) > maxRESPElements {
			return nil, true, fmt.Errorf("RESP inline argument count %d exceeds %d limit", len(fields), maxRESPElements)
		}
		args := make([][]byte, len(fields))
		for i, f := range fields {
			args[i] = []byte(f)
		}
		return args, true, nil
	}
}

const (
	maxRESPLineBytes    = 64 * 1024
	maxRESPElements     = 65536
	maxRESPCommandBytes = 64 * 1024 * 1024
	maxRESPDepth        = 8
)

type respLimits struct {
	maxElements int
	maxBytes    int
}

func (l *respLimits) consumeBytes(n int) error {
	if l == nil {
		return nil
	}
	if n < 0 || n > l.maxBytes {
		return errors.New("RESP command exceeds 64 MiB limit")
	}
	l.maxBytes -= n
	return nil
}

func (l *respLimits) consumeElements(n int) error {
	if l == nil {
		return nil
	}
	if n < 0 || n > l.maxElements {
		return fmt.Errorf("RESP element count %d exceeds %d limit", n, maxRESPElements)
	}
	l.maxElements -= n
	return nil
}

func readRESPLine(r *bufio.Reader) ([]byte, error) {
	var buf []byte
	for {
		frag, err := r.ReadSlice('\n')
		if len(buf)+len(frag) > maxRESPLineBytes {
			return nil, errors.New("RESP line exceeds 64 KiB limit")
		}
		buf = append(buf, frag...)
		if err == nil {
			break
		}
		if err != bufio.ErrBufferFull {
			return nil, err
		}
	}
	if len(buf) >= 2 && buf[len(buf)-2] == '\r' {
		return buf[:len(buf)-2], nil
	}
	if len(buf) >= 1 && buf[len(buf)-1] == '\n' {
		return buf[:len(buf)-1], nil
	}
	return buf, nil
}

func readRESPLineLimited(r *bufio.Reader, lim *respLimits) ([]byte, error) {
	line, err := readRESPLine(r)
	if err != nil {
		return nil, err
	}
	if err := lim.consumeBytes(len(line) + 2); err != nil {
		return nil, err
	}
	return line, nil
}

func readRESP(r *bufio.Reader) (respValue, error) {
	return readRESPDepth(r, 0, nil)
}

func readRESPDepth(r *bufio.Reader, depth int, lim *respLimits) (respValue, error) {
	if depth > maxRESPDepth {
		return respValue{}, fmt.Errorf("RESP nesting depth exceeds %d limit", maxRESPDepth)
	}
	prefix, err := r.ReadByte()
	if err != nil {
		return respValue{}, err
	}
	if err := lim.consumeBytes(1); err != nil {
		return respValue{}, err
	}
	switch prefix {
	case '+', '-', ':', '_', ',', '#', '(':
		line, err := readRESPLineLimited(r, lim)
		return respValue{kind: prefix, str: line}, err
	case '$', '!', '=':
		line, err := readRESPLineLimited(r, lim)
		if err != nil {
			return respValue{}, err
		}
		n, err := strconv.Atoi(string(line))
		if err != nil {
			return respValue{}, fmt.Errorf("invalid RESP bulk length %q: %w", line, err)
		}
		if n < 0 {
			return respValue{kind: prefix, isNil: true}, nil
		}
		if n > 64*1024*1024 {
			return respValue{}, errors.New("RESP bulk string exceeds 64MB limit")
		}
		if err := lim.consumeBytes(n + 2); err != nil {
			return respValue{}, err
		}
		buf := make([]byte, n+2)
		if _, err := io.ReadFull(r, buf); err != nil {
			return respValue{}, err
		}
		return respValue{kind: prefix, str: buf[:n]}, nil
	case '*', '~', '>':
		line, err := readRESPLineLimited(r, lim)
		if err != nil {
			return respValue{}, err
		}
		n, err := strconv.Atoi(string(line))
		if err != nil {
			return respValue{}, fmt.Errorf("invalid RESP array count %q: %w", line, err)
		}
		if n < 0 {
			return respValue{kind: prefix, isNil: true}, nil
		}
		if n > maxRESPElements {
			return respValue{}, fmt.Errorf("RESP array count %d exceeds %d limit", n, maxRESPElements)
		}
		if err := lim.consumeElements(n); err != nil {
			return respValue{}, err
		}
		initCap := n
		if initCap > 1024 {
			initCap = 1024
		}
		arr := make([]respValue, 0, initCap)
		for i := 0; i < n; i++ {
			elem, err := readRESPDepth(r, depth+1, lim)
			if err != nil {
				return respValue{}, err
			}
			arr = append(arr, elem)
		}
		return respValue{kind: prefix, arr: arr}, nil
	case '|':
		line, err := readRESPLineLimited(r, lim)
		if err != nil {
			return respValue{}, err
		}
		n, err := strconv.Atoi(string(line))
		if err != nil || n < 0 {
			return respValue{}, fmt.Errorf("invalid RESP3 attribute count %q: %w", line, err)
		}
		if n > maxRESPElements/2 {
			return respValue{}, fmt.Errorf("RESP3 attribute count %d exceeds %d limit", n, maxRESPElements/2)
		}
		total := n * 2
		if err := lim.consumeElements(total); err != nil {
			return respValue{}, err
		}
		initCap := total
		if initCap > 1024 {
			initCap = 1024
		}
		pairs := make([]respValue, 0, initCap)
		for i := 0; i < total; i++ {
			elem, err := readRESPDepth(r, depth+1, lim)
			if err != nil {
				return respValue{}, err
			}
			pairs = append(pairs, elem)
		}
		inner, err := readRESPDepth(r, depth+1, lim)
		if err != nil {
			return respValue{}, err
		}
		inner.attrs = append([]respAttrBlock{{pairs: pairs}}, inner.attrs...)
		return inner, nil
	case '%':
		line, err := readRESPLineLimited(r, lim)
		if err != nil {
			return respValue{}, err
		}
		n, err := strconv.Atoi(string(line))
		if err != nil {
			return respValue{}, fmt.Errorf("invalid RESP map count %q: %w", line, err)
		}
		if n < 0 {
			return respValue{kind: prefix, isNil: true}, nil
		}
		if n > maxRESPElements/2 {
			return respValue{}, fmt.Errorf("RESP map count %d exceeds %d limit", n, maxRESPElements/2)
		}
		total := n * 2
		if err := lim.consumeElements(total); err != nil {
			return respValue{}, err
		}
		initCap := total
		if initCap > 1024 {
			initCap = 1024
		}
		arr := make([]respValue, 0, initCap)
		for i := 0; i < total; i++ {
			elem, err := readRESPDepth(r, depth+1, lim)
			if err != nil {
				return respValue{}, err
			}
			arr = append(arr, elem)
		}
		return respValue{kind: prefix, arr: arr}, nil
	default:
		// Unexpected non-prefixed line fallback
		rest, err := readRESPLineLimited(r, lim)
		if err != nil {
			return respValue{}, err
		}
		full := append([]byte{prefix}, rest...)
		return respValue{kind: '+', str: full}, nil
	}
}

func writeRESPArrayArgs(w *bufio.Writer, args [][]byte) error {
	if _, err := fmt.Fprintf(w, "*%d\r\n", len(args)); err != nil {
		return err
	}
	for _, a := range args {
		if a == nil {
			if _, err := w.WriteString("$-1\r\n"); err != nil {
				return err
			}
			continue
		}
		if _, err := fmt.Fprintf(w, "$%d\r\n", len(a)); err != nil {
			return err
		}
		if _, err := w.Write(a); err != nil {
			return err
		}
		if _, err := w.WriteString("\r\n"); err != nil {
			return err
		}
	}
	return nil
}

func writeRESP(w *bufio.Writer, v *respValue) error {
	for _, blk := range v.attrs {
		if _, err := fmt.Fprintf(w, "|%d\r\n", len(blk.pairs)/2); err != nil {
			return err
		}
		for i := range blk.pairs {
			if err := writeRESP(w, &blk.pairs[i]); err != nil {
				return err
			}
		}
	}
	switch v.kind {
	case '+', '-', ':', '_', ',', '#', '(':
		if err := w.WriteByte(v.kind); err != nil {
			return err
		}
		if _, err := w.Write(v.str); err != nil {
			return err
		}
		_, err := w.WriteString("\r\n")
		return err
	case '$', '!', '=':
		if v.isNil {
			_, err := fmt.Fprintf(w, "%c-1\r\n", v.kind)
			return err
		}
		if _, err := fmt.Fprintf(w, "%c%d\r\n", v.kind, len(v.str)); err != nil {
			return err
		}
		if _, err := w.Write(v.str); err != nil {
			return err
		}
		_, err := w.WriteString("\r\n")
		return err
	case '*', '~', '>':
		if v.isNil {
			_, err := fmt.Fprintf(w, "%c-1\r\n", v.kind)
			return err
		}
		if _, err := fmt.Fprintf(w, "%c%d\r\n", v.kind, len(v.arr)); err != nil {
			return err
		}
		for i := range v.arr {
			if err := writeRESP(w, &v.arr[i]); err != nil {
				return err
			}
		}
		return nil
	case '%':
		if v.isNil {
			_, err := w.WriteString("%-1\r\n")
			return err
		}
		if _, err := fmt.Fprintf(w, "%%%d\r\n", len(v.arr)/2); err != nil {
			return err
		}
		for i := range v.arr {
			if err := writeRESP(w, &v.arr[i]); err != nil {
				return err
			}
		}
		return nil
	default:
		return fmt.Errorf("unknown RESP kind %q", v.kind)
	}
}
