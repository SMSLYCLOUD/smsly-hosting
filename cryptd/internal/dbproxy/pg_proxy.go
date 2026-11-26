package dbproxy

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"strings"
	"time"

	"smsly-hosting/cryptd/internal/epoch"
)

const (
	pgSSLRequestCode    uint32 = 80877103
	pgGSSENCRequestCode uint32 = 80877104
	pgCancelRequestCode uint32 = 80877102
	pgProtocolV3        uint32 = 196608
	maxStartupBytes     int    = 65536
)

// PGProxyConfig configures a PostgreSQL wire v3 cryptographic point-of-contact proxy.
type PGProxyConfig struct {
	ListenAddr          string
	DefaultUpstream     string
	DialTimeout         time.Duration
	ClientTLS           *tls.Config
	UpstreamTLS         *tls.Config
	UpstreamTLSOptional bool
	ExplicitCA          bool
}

// PGProxy sits at the Postgres point of contact (in front of pgcat / pgbouncer / postgres),
// enforcing service/epoch lock checks before upstream dialing and severing active TCP sessions
// immediately when a service or epoch locks.
type PGProxy struct {
	cfg      PGProxyConfig
	epochs   *epoch.Manager
	router   *Router
	listener net.Listener
}

// NewPGProxy creates a PostgreSQL wire proxy.
func NewPGProxy(cfg PGProxyConfig, epochs *epoch.Manager, router *Router) (*PGProxy, error) {
	if cfg.DialTimeout <= 0 {
		cfg.DialTimeout = 10 * time.Second
	}
	return &PGProxy{
		cfg:    cfg,
		epochs: epochs,
		router: router,
	}, nil
}

// ListenAndServe binds the proxy listener and serves until ctx is cancelled.
func (p *PGProxy) ListenAndServe(ctx context.Context) error {
	ln, err := net.Listen("tcp", p.cfg.ListenAddr)
	if err != nil {
		return err
	}
	p.listener = ln
	return p.Serve(ctx, ln)
}

// Serve accepts connections on an existing net.Listener.
func (p *PGProxy) Serve(ctx context.Context, ln net.Listener) error {
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

func (p *PGProxy) handleConn(ctx context.Context, rawClient net.Conn) {
	defer rawClient.Close()

	client, startupFrame, params, isCancel, err := p.readStartup(rawClient)
	if err != nil {
		return
	}
	if client != rawClient {
		defer client.Close()
	}
	if isCancel {
		// Only forward valid 16-byte Postgres CancelRequest packets when DefaultUpstream
		// is configured, the vault is active/grace, and the default route is authorized.
		upstreamAddr := strings.TrimSpace(p.cfg.DefaultUpstream)
		if len(startupFrame) != 16 || upstreamAddr == "" {
			return
		}
		st := p.epochs.Status()
		if st.State != epoch.StateActive && st.State != epoch.StateGrace {
			return
		}
		target := p.router.ResolvePostgres("", "")
		if target.ServiceID == "" {
			return
		}
		if _, _, authErr := p.epochs.AuthorizeDBAccess(target.ServiceID); authErr != nil {
			return
		}
		if up, derr := (&net.Dialer{Timeout: p.cfg.DialTimeout}).DialContext(ctx, "tcp", upstreamAddr); derr == nil {
			_ = up.SetDeadline(time.Now().Add(p.cfg.DialTimeout))
			_, _ = up.Write(startupFrame)
			_ = up.Close()
		}
		return
	}

	pgUser := params["user"]
	pgDB := params["database"]
	target := p.router.ResolvePostgres(pgUser, pgDB)

	// Point-of-contact lock & opt-in evaluation:
	// - Non-opted services pass cleanly when policy allows.
	// - Opted-in services require unlocked lease and non-LOCKED global epoch.
	optedIn, _, err := p.epochs.AuthorizeDBAccess(target.ServiceID)
	if err != nil {
		code := "57P03" // cannot_connect_now
		if errors.Is(err, epoch.ErrServiceLocked) {
			code = "28000" // invalid_authorization_specification / locked
		}
		_ = writePGErrorResponse(client, code, fmt.Sprintf("trulay-grid: Postgres connection blocked (%v)", err))
		return
	}
	_ = optedIn

	upstreamAddr := target.UpstreamPG
	if upstreamAddr == "" {
		upstreamAddr = p.cfg.DefaultUpstream
	}
	if upstreamAddr == "" {
		_ = writePGErrorResponse(client, "08006", "trulay-grid: no upstream Postgres configured")
		return
	}

	dialer := &net.Dialer{Timeout: p.cfg.DialTimeout}
	upstream, err := dialer.DialContext(ctx, "tcp", upstreamAddr)
	if err != nil {
		_ = writePGErrorResponse(client, "08006", fmt.Sprintf("trulay-grid: upstream dial failed: %v", err))
		return
	}
	defer upstream.Close()

	if p.cfg.UpstreamTLS != nil {
		upstreamOptional := p.cfg.UpstreamTLSOptional && isLoopbackOrPrivateEndpoint(upstreamAddr)
		tlsUpstream, uerr := negotiateUpstreamTLS(upstream, p.cfg.UpstreamTLS, upstreamOptional, p.cfg.ExplicitCA, upstreamAddr)
		if uerr != nil {
			_ = writePGErrorResponse(client, "08006", fmt.Sprintf("trulay-grid: upstream TLS failed: %v", uerr))
			return
		}
		if tlsUpstream != upstream {
			upstream = tlsUpstream
			defer upstream.Close()
		}
	}

	// Track connection pair under serviceID so ForceLockdown / LockService severs active sessions immediately.
	pair := connPair{a: client, b: upstream}
	p.router.TrackConn(target.ServiceID, pair)
	defer p.router.UntrackConn(target.ServiceID, pair)

	// Re-check right after tracking to avoid any lock race between AuthorizeDBAccess and TrackConn.
	if _, _, rerr := p.epochs.AuthorizeDBAccess(target.ServiceID); rerr != nil {
		_ = writePGErrorResponse(client, "57P03", fmt.Sprintf("trulay-grid: locked during handshake (%v)", rerr))
		return
	}

	_ = upstream.SetWriteDeadline(time.Now().Add(15 * time.Second))
	if _, err := upstream.Write(startupFrame); err != nil {
		return
	}
	_ = upstream.SetWriteDeadline(time.Time{})

	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(upstream, client)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(client, upstream)
		done <- struct{}{}
	}()
	select {
	case <-done:
	case <-ctx.Done():
	}
}

func (p *PGProxy) readStartup(conn net.Conn) (clientConn net.Conn, rawFrame []byte, params map[string]string, isCancel bool, err error) {
	cur := conn
	for attempt := 0; attempt < 4; attempt++ {
		_ = cur.SetReadDeadline(time.Now().Add(15 * time.Second))
		var lenBuf [4]byte
		if _, err := io.ReadFull(cur, lenBuf[:]); err != nil {
			if cur != conn {
				_ = cur.Close()
			}
			return nil, nil, nil, false, err
		}
		pktLen := int(binary.BigEndian.Uint32(lenBuf[:]))
		if pktLen < 8 || pktLen > maxStartupBytes {
			if cur != conn {
				_ = cur.Close()
			}
			return nil, nil, nil, false, fmt.Errorf("invalid Postgres startup packet length %d", pktLen)
		}
		payload := make([]byte, pktLen-4)
		if _, err := io.ReadFull(cur, payload); err != nil {
			if cur != conn {
				_ = cur.Close()
			}
			return nil, nil, nil, false, err
		}
		_ = cur.SetReadDeadline(time.Time{})

		code := binary.BigEndian.Uint32(payload[:4])
		frame := make([]byte, pktLen)
		copy(frame[:4], lenBuf[:])
		copy(frame[4:], payload)

		switch code {
		case pgSSLRequestCode:
			if p.cfg.ClientTLS != nil {
				if _, werr := cur.Write([]byte{'S'}); werr != nil {
					if cur != conn {
						_ = cur.Close()
					}
					return nil, nil, nil, false, werr
				}
				_ = cur.SetDeadline(time.Now().Add(15 * time.Second))
				tlsConn := tls.Server(cur, p.cfg.ClientTLS)
				if herr := tlsConn.Handshake(); herr != nil {
					_ = tlsConn.Close()
					return nil, nil, nil, false, herr
				}
				_ = tlsConn.SetDeadline(time.Time{})
				cur = tlsConn
			} else {
				if _, werr := cur.Write([]byte{'N'}); werr != nil {
					if cur != conn {
						_ = cur.Close()
					}
					return nil, nil, nil, false, werr
				}
			}
			continue
		case pgGSSENCRequestCode:
			// Decline GSSAPI encryption so modern libpq/psycopg/asyncpg clients
			// proceed cleanly to SSLRequest or plain StartupMessage.
			if _, werr := cur.Write([]byte{'N'}); werr != nil {
				if cur != conn {
					_ = cur.Close()
				}
				return nil, nil, nil, false, werr
			}
			continue
		case pgCancelRequestCode:
			return cur, frame, nil, true, nil
		case pgProtocolV3:
			parsed := parsePGStartupParams(payload[4:])
			return cur, frame, parsed, false, nil
		default:
			_ = writePGErrorResponse(cur, "0A000", fmt.Sprintf("unsupported Postgres wire protocol code %d", code))
			if cur != conn {
				_ = cur.Close()
			}
			return nil, nil, nil, false, fmt.Errorf("unsupported protocol code %d", code)
		}
	}
	if cur != conn {
		_ = cur.Close()
	}
	return nil, nil, nil, false, errors.New("exceeded maximum startup negotiation steps")
}

func parsePGStartupParams(b []byte) map[string]string {
	out := make(map[string]string)
	tokens := bytes.Split(b, []byte{0})
	for i := 0; i+1 < len(tokens); i += 2 {
		k := strings.TrimSpace(string(tokens[i]))
		v := strings.TrimSpace(string(tokens[i+1]))
		if k == "" {
			break
		}
		out[k] = v
	}
	return out
}

func isLoopbackOrPrivateEndpoint(addr string) bool {
	host := strings.TrimSpace(addr)
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	host = strings.Trim(host, "[]")
	if strings.EqualFold(host, "localhost") {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback() || ip.IsPrivate()
	}
	if ips, err := net.LookupIP(host); err == nil && len(ips) > 0 {
		for _, ip := range ips {
			if !ip.IsLoopback() && !ip.IsPrivate() {
				return false
			}
		}
		return true
	}
	return false
}

func negotiateUpstreamTLS(conn net.Conn, cfg *tls.Config, optional, explicitCA bool, upstreamAddr string) (net.Conn, error) {
	_ = conn.SetDeadline(time.Now().Add(15 * time.Second))
	var sslReq [8]byte
	binary.BigEndian.PutUint32(sslReq[:4], 8)
	binary.BigEndian.PutUint32(sslReq[4:], pgSSLRequestCode)
	if _, err := conn.Write(sslReq[:]); err != nil {
		return nil, err
	}
	var resp [1]byte
	if _, err := io.ReadFull(conn, resp[:]); err != nil {
		return nil, err
	}
	if resp[0] != 'S' {
		if optional && resp[0] == 'N' {
			_ = conn.SetDeadline(time.Time{})
			return conn, nil
		}
		return nil, errors.New("upstream Postgres refused TLS negotiation")
	}
	tlsCfg := cfg.Clone()
	if optional && !explicitCA && isLoopbackOrPrivateEndpoint(upstreamAddr) {
		tlsCfg.InsecureSkipVerify = true // #nosec G402 -- internal docker/loopback Postgres TLS without public IP SAN
	}
	if tlsCfg.ServerName == "" {
		if host, _, err := net.SplitHostPort(upstreamAddr); err == nil {
			host = strings.Trim(host, "[]")
			if host != "" && net.ParseIP(host) == nil {
				tlsCfg.ServerName = host
			}
		}
	}
	tlsConn := tls.Client(conn, tlsCfg)
	if err := tlsConn.Handshake(); err != nil {
		_ = tlsConn.Close()
		return nil, err
	}
	_ = tlsConn.SetDeadline(time.Time{})
	return tlsConn, nil
}

// writePGErrorResponse writes an authentic PostgreSQL v3 ErrorResponse ('E') frame
// so libpq / psycopg / psql receive a clean structured database error message.
func writePGErrorResponse(w io.Writer, sqlstate, message string) error {
	var body bytes.Buffer
	writeField := func(code byte, val string) {
		body.WriteByte(code)
		body.WriteString(val)
		body.WriteByte(0)
	}
	writeField('S', "FATAL")
	writeField('V', "FATAL")
	writeField('C', sqlstate)
	writeField('M', message)
	body.WriteByte(0) // terminator

	var frame bytes.Buffer
	frame.WriteByte('E')
	var lenBuf [4]byte
	binary.BigEndian.PutUint32(lenBuf[:], uint32(4+body.Len()))
	frame.Write(lenBuf[:])
	frame.Write(body.Bytes())

	if _, err := w.Write(frame.Bytes()); err != nil {
		log.Printf("DEBUG [cryptd/pgproxy]: failed to send PG ErrorResponse: %v", err)
		return err
	}
	return nil
}
