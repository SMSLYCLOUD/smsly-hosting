package dbproxy

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/binary"
	"io"
	"math/big"
	"net"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/epoch"
	"smsly-hosting/cryptd/internal/vault"
)

func setupTestController(t *testing.T) (*epoch.Manager, *Router) {
	t.Helper()
	store, err := crypto.OpenKeyStore(filepath.Join(t.TempDir(), "ks"))
	if err != nil {
		t.Fatalf("OpenKeyStore: %v", err)
	}
	prov, err := vault.NewProvider(vault.Config{
		ProviderName:  "dev_memory",
		AllowDevVault: true,
		DevMasterKey:  strings.Repeat("ab", 32),
	})
	if err != nil {
		t.Fatalf("NewProvider: %v", err)
	}
	mgr, err := epoch.NewManager(epoch.DefaultConfig(), prov, store)
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	if err := mgr.SyncNow(context.Background()); err != nil {
		t.Fatalf("SyncNow: %v", err)
	}
	router := NewRouter(ServiceTarget{
		ServiceID:        crypto.ControlPlaneServiceID,
		ProjectID:        "control-plane",
		OptedIn:          false,
		EncryptRedisVals: false,
	})
	mgr.SetCallbacks(
		func(string) { router.CloseAllConns() },
		func(svcID, _ string) { router.CloseServiceConns(svcID) },
	)
	return mgr, router
}

func encodePGStartup(user, db string) []byte {
	var body bytes.Buffer
	var proto [4]byte
	binary.BigEndian.PutUint32(proto[:], pgProtocolV3)
	body.Write(proto[:])
	body.WriteString("user\x00" + user + "\x00database\x00" + db + "\x00\x00")
	pkt := make([]byte, 4+body.Len())
	binary.BigEndian.PutUint32(pkt[:4], uint32(len(pkt)))
	copy(pkt[4:], body.Bytes())
	return pkt
}

func TestPGProxyGateAndInstantSocketSeverOnLock(t *testing.T) {
	mgr, router := setupTestController(t)

	// Opt-in svc-pg and bind its Postgres role
	if err := mgr.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID: "svc-pg",
		ProjectID: "proj-pg",
		DBRoles:   []string{"role_svc_pg"},
	}, true); err != nil {
		t.Fatalf("RegisterService: %v", err)
	}
	if _, err := mgr.UnlockService("svc-pg", "proj-pg", time.Hour, false); err != nil {
		t.Fatalf("UnlockService: %v", err)
	}

	// Mock upstream Postgres server
	upLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen upstream pg: %v", err)
	}
	defer upLn.Close()

	go func() {
		for {
			c, err := upLn.Accept()
			if err != nil {
				return
			}
			go func(conn net.Conn) {
				defer conn.Close()
				buf := make([]byte, 4096)
				n, err := conn.Read(buf)
				if err != nil {
					return
				}
				if n > 0 {
					// Send AuthenticationOk ('R', len=8, 0) + echo loop
					_, _ = conn.Write([]byte{'R', 0, 0, 0, 8, 0, 0, 0, 0})
					_, _ = io.Copy(conn, conn)
				}
			}(c)
		}
	}()

	router.Register(ServiceTarget{
		ServiceID:  "svc-pg",
		ProjectID:  "proj-pg",
		DBRoles:    []string{"role_svc_pg"},
		UpstreamPG: upLn.Addr().String(),
		OptedIn:    true,
	})

	proxy, err := NewPGProxy(PGProxyConfig{
		ListenAddr:      "127.0.0.1:0",
		DefaultUpstream: upLn.Addr().String(),
	}, mgr, router)
	if err != nil {
		t.Fatalf("NewPGProxy: %v", err)
	}
	proxyLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen proxy pg: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.Serve(ctx, proxyLn) }()

	// 1. Unlocked connection succeeds through proxy
	client, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	// Send SSLRequest first, expect 'N', then StartupMessage
	var sslReq [8]byte
	binary.BigEndian.PutUint32(sslReq[:4], 8)
	binary.BigEndian.PutUint32(sslReq[4:], pgSSLRequestCode)
	_, _ = client.Write(sslReq[:])
	var sslReply [1]byte
	if _, err := io.ReadFull(client, sslReply[:]); err != nil || sslReply[0] != 'N' {
		t.Fatalf("unexpected SSLRequest reply: %v %q", err, sslReply[0])
	}
	_, _ = client.Write(encodePGStartup("role_svc_pg", "appdb"))
	var authReply [9]byte
	if _, err := io.ReadFull(client, authReply[:]); err != nil || authReply[0] != 'R' {
		t.Fatalf("expected AuthenticationOk 'R', got %v %v", err, authReply)
	}

	// 2. Locking svc-pg immediately severs the open TCP connection!
	if err := mgr.LockService("svc-pg", "compliance lock"); err != nil {
		t.Fatalf("LockService: %v", err)
	}
	_ = client.SetReadDeadline(time.Now().Add(2 * time.Second))
	one := make([]byte, 1)
	if _, err := client.Read(one); err == nil {
		t.Fatal("expected live Postgres connection to be severed on LockService")
	}
	_ = client.Close()

	// 3. New connection while locked receives genuine Postgres ErrorResponse ('E')
	lockedClient, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial locked proxy: %v", err)
	}
	defer lockedClient.Close()
	_, _ = lockedClient.Write(encodePGStartup("role_svc_pg", "appdb"))
	_ = lockedClient.SetReadDeadline(time.Now().Add(2 * time.Second))
	errHeader := make([]byte, 5)
	if _, err := io.ReadFull(lockedClient, errHeader); err != nil {
		t.Fatalf("expected ErrorResponse frame: %v", err)
	}
	if errHeader[0] != 'E' {
		t.Fatalf("expected Postgres 'E' ErrorResponse, got %q", errHeader[0])
	}
	remLen := int(binary.BigEndian.Uint32(errHeader[1:])) - 4
	errBody := make([]byte, remLen)
	_, _ = io.ReadFull(lockedClient, errBody)
	if !bytes.Contains(errBody, []byte("trulay-grid")) {
		t.Fatalf("expected trulay-grid lock message in ErrorResponse, got %q", errBody)
	}
}

// Minimal in-memory mock Redis server storing EXACT wire bytes received from proxy.
type mockRedisServer struct {
	mu   sync.Mutex
	kv   map[string][]byte
	hash map[string]map[string][]byte
	list map[string][][]byte
}

func startMockRedis(t *testing.T) (string, *mockRedisServer, func()) {
	t.Helper()
	srv := &mockRedisServer{
		kv:   make(map[string][]byte),
		hash: make(map[string]map[string][]byte),
		list: make(map[string][][]byte),
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen mock redis: %v", err)
	}
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go srv.handle(c)
		}
	}()
	return ln.Addr().String(), srv, func() { _ = ln.Close() }
}

func (s *mockRedisServer) execCmd(args [][]byte) respValue {
	cmd := strings.ToUpper(string(args[0]))
	s.mu.Lock()
	defer s.mu.Unlock()
	switch cmd {
	case "AUTH":
		if len(args) >= 3 && string(args[len(args)-1]) == "wrongpass" {
			return respValue{kind: '-', str: []byte("WRONGPASS invalid username-password pair")}
		}
		return respValue{kind: '+', str: []byte("OK")}
	case "PING":
		if len(args) == 1 {
			return respValue{kind: '+', str: []byte("PONG")}
		}
		return respValue{kind: '+', str: []byte("OK")}
	case "SET":
		retOld := false
		for i := 3; i < len(args); i++ {
			if strings.EqualFold(string(args[i]), "GET") {
				retOld = true
				break
			}
		}
		prev, hadPrev := s.kv[string(args[1])]
		prevCopy := append([]byte(nil), prev...)
		s.kv[string(args[1])] = append([]byte(nil), args[2]...)
		if retOld {
			if !hadPrev {
				return respValue{kind: '$', isNil: true}
			}
			return respValue{kind: '$', str: prevCopy}
		}
		return respValue{kind: '+', str: []byte("OK")}
	case "MSET":
		for i := 1; i+1 < len(args); i += 2 {
			s.kv[string(args[i])] = append([]byte(nil), args[i+1]...)
		}
		return respValue{kind: '+', str: []byte("OK")}
	case "MSETNX":
		for i := 1; i+1 < len(args); i += 2 {
			if _, ok := s.kv[string(args[i])]; ok {
				return respValue{kind: ':', str: []byte("0")}
			}
		}
		for i := 1; i+1 < len(args); i += 2 {
			s.kv[string(args[i])] = append([]byte(nil), args[i+1]...)
		}
		return respValue{kind: ':', str: []byte("1")}
	case "GET", "GETEX":
		v, ok := s.kv[string(args[1])]
		if !ok {
			return respValue{kind: '$', isNil: true}
		}
		if cmd == "GET" && strings.HasSuffix(string(args[1]), ":attr") {
			return respValue{
				kind: '$',
				str:  v,
				attrs: []respAttrBlock{{pairs: []respValue{
					{kind: '+', str: []byte("ttl")},
					{kind: ':', str: []byte("42")},
				}}},
			}
		}
		return respValue{kind: '$', str: v}
	case "MGET":
		arr := make([]respValue, len(args)-1)
		for i := 1; i < len(args); i++ {
			v, ok := s.kv[string(args[i])]
			if !ok {
				arr[i-1] = respValue{kind: '$', isNil: true}
			} else {
				arr[i-1] = respValue{kind: '$', str: v}
			}
		}
		return respValue{kind: '*', arr: arr}
	case "HSET":
		h := s.hash[string(args[1])]
		if h == nil {
			h = make(map[string][]byte)
			s.hash[string(args[1])] = h
		}
		count := 0
		for i := 2; i+1 < len(args); i += 2 {
			if _, exists := h[string(args[i])]; !exists {
				count++
			}
			h[string(args[i])] = append([]byte(nil), args[i+1]...)
		}
		return respValue{kind: ':', str: []byte(string(rune('0' + count)))}
	case "HGET":
		h := s.hash[string(args[1])]
		if h == nil {
			return respValue{kind: '$', isNil: true}
		}
		if v, ok := h[string(args[2])]; !ok {
			return respValue{kind: '$', isNil: true}
		} else {
			return respValue{kind: '$', str: v}
		}
	case "HVALS":
		h := s.hash[string(args[1])]
		arr := make([]respValue, 0, len(h))
		for _, v := range h {
			arr = append(arr, respValue{kind: '$', str: v})
		}
		return respValue{kind: '*', arr: arr}
	case "HGETALL":
		h := s.hash[string(args[1])]
		var arr []respValue
		for k, v := range h {
			arr = append(arr,
				respValue{kind: '$', str: []byte(k)},
				respValue{kind: '$', str: v},
			)
		}
		return respValue{kind: '*', arr: arr}
	case "LPUSH", "LPUSHX":
		k := string(args[1])
		for i := 2; i < len(args); i++ {
			s.list[k] = append([][]byte{append([]byte(nil), args[i]...)}, s.list[k]...)
		}
		return respValue{kind: ':', str: []byte("1")}
	case "LSET":
		k := string(args[1])
		if len(s.list[k]) > 0 {
			s.list[k][0] = append([]byte(nil), args[3]...)
		}
		return respValue{kind: '+', str: []byte("OK")}
	case "LINDEX":
		items := s.list[string(args[1])]
		if len(items) == 0 {
			return respValue{kind: '$', isNil: true}
		}
		return respValue{kind: '$', str: items[0]}
	case "LPOP":
		k := string(args[1])
		items := s.list[k]
		if len(items) == 0 {
			return respValue{kind: '$', isNil: true}
		}
		v := items[0]
		s.list[k] = items[1:]
		return respValue{kind: '$', str: v}
	case "BLPOP":
		for i := 1; i+1 < len(args); i++ {
			k := string(args[i])
			items := s.list[k]
			if len(items) > 0 {
				v := items[0]
				s.list[k] = items[1:]
				return respValue{kind: '*', arr: []respValue{
					{kind: '$', str: []byte(k)},
					{kind: '$', str: v},
				}}
			}
		}
		return respValue{kind: '*', isNil: true}
	case "RPOPLPUSH":
		src := string(args[1])
		dst := string(args[2])
		items := s.list[src]
		if len(items) == 0 {
			return respValue{kind: '$', isNil: true}
		}
		v := items[len(items)-1]
		s.list[src] = items[:len(items)-1]
		s.list[dst] = append([][]byte{v}, s.list[dst]...)
		return respValue{kind: '$', str: v}
	default:
		return respValue{kind: '+', str: []byte("OK")}
	}
}

func (s *mockRedisServer) handle(c net.Conn) {
	defer c.Close()
	r := bufio.NewReader(c)
	w := bufio.NewWriter(c)
	var inMulti bool
	var queue [][][]byte
	for {
		args, _, err := readRESPCommand(r)
		if err != nil {
			return
		}
		if len(args) == 0 {
			continue
		}
		cmd := strings.ToUpper(string(args[0]))
		var rep respValue
		switch {
		case cmd == "MULTI":
			inMulti = true
			queue = nil
			rep = respValue{kind: '+', str: []byte("OK")}
		case cmd == "DISCARD":
			inMulti = false
			queue = nil
			rep = respValue{kind: '+', str: []byte("OK")}
		case cmd == "EXEC":
			q := queue
			inMulti = false
			queue = nil
			arr := make([]respValue, len(q))
			for i, qa := range q {
				arr[i] = s.execCmd(qa)
			}
			rep = respValue{kind: '*', arr: arr}
		case inMulti:
			queue = append(queue, args)
			rep = respValue{kind: '+', str: []byte("QUEUED")}
		default:
			rep = s.execCmd(args)
		}
		_ = writeRESP(w, &rep)
		_ = w.Flush()
	}
}

func sendRedisCmd(t *testing.T, r *bufio.Reader, w *bufio.Writer, args ...string) respValue {
	t.Helper()
	bargs := make([][]byte, len(args))
	for i, a := range args {
		bargs[i] = []byte(a)
	}
	if err := writeRESPArrayArgs(w, bargs); err != nil {
		t.Fatalf("writeRESPArrayArgs: %v", err)
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}
	val, err := readRESP(r)
	if err != nil {
		t.Fatalf("readRESP: %v", err)
	}
	return val
}

func TestRedisProxyTransparentPerServiceEncryptionAndLock(t *testing.T) {
	mgr, router := setupTestController(t)
	upAddr, mockSrv, cleanupUp := startMockRedis(t)
	defer cleanupUp()

	// Opt-in svc-red-a; leave svc-red-optout disabled!
	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID: "svc-red-a",
		ProjectID: "proj-1",
	}, true)
	_, _ = mgr.UnlockService("svc-red-a", "proj-1", time.Hour, false)
	router.Register(ServiceTarget{
		ServiceID:        "svc-red-a",
		ProjectID:        "proj-1",
		RedisUsers:       []string{"user_a"},
		RedisPrefixes:    []string{"svca:"},
		UpstreamRedis:    upAddr,
		OptedIn:          true,
		EncryptRedisVals: true,
	})

	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID: "svc-red-optout",
		ProjectID: "proj-1",
	}, false)
	router.Register(ServiceTarget{
		ServiceID:        "svc-red-optout",
		ProjectID:        "proj-1",
		RedisUsers:       []string{"user_optout"},
		RedisPrefixes:    []string{"plain:"},
		UpstreamRedis:    upAddr,
		OptedIn:          false,
		EncryptRedisVals: false,
	})

	proxy, err := NewRedisProxy(RedisProxyConfig{
		ListenAddr:         "127.0.0.1:0",
		DefaultUpstream:    upAddr,
		TransparentEncrypt: true,
	}, mgr, router)
	if err != nil {
		t.Fatalf("NewRedisProxy: %v", err)
	}
	proxyLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen proxy redis: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.Serve(ctx, proxyLn) }()

	// Client A (opted-in): SET / GET / MSET / MGET / HSET / HGET / HGETALL / LPUSH / LPOP
	cA, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial redis proxy: %v", err)
	}
	defer cA.Close()
	rA := bufio.NewReader(cA)
	wA := bufio.NewWriter(cA)

	if rep := sendRedisCmd(t, rA, wA, "AUTH", "user_a", "pass"); string(rep.str) != "OK" {
		t.Fatalf("AUTH failed: %+v", rep)
	}
	if rep := sendRedisCmd(t, rA, wA, "SET", "svca:api_key", "sk_live_987654"); string(rep.str) != "OK" {
		t.Fatalf("SET failed: %+v", rep)
	}

	// Verify Redis server RAM holds ciphertext ONLY, NOT plaintext!
	mockSrv.mu.Lock()
	storedRaw := mockSrv.kv["svca:api_key"]
	mockSrv.mu.Unlock()
	if !bytes.HasPrefix(storedRaw, []byte(crypto.RedisValuePrefix)) || bytes.Contains(storedRaw, []byte("sk_live_987654")) {
		t.Fatalf("expected encrypted GRD1 ciphertext in Redis memory, got %q", storedRaw)
	}

	// Client A reads back transparent plaintext!
	got := sendRedisCmd(t, rA, wA, "GET", "svca:api_key")
	if string(got.str) != "sk_live_987654" {
		t.Fatalf("GET did not transparently decrypt: got %q", got.str)
	}

	// Hash & List tests
	_ = sendRedisCmd(t, rA, wA, "HSET", "svca:sess", "token", "jwt-secret-value")
	mockSrv.mu.Lock()
	hashStored := mockSrv.hash["svca:sess"]["token"]
	mockSrv.mu.Unlock()
	if !bytes.HasPrefix(hashStored, []byte(crypto.RedisValuePrefix)) {
		t.Fatalf("expected encrypted hash field in Redis, got %q", hashStored)
	}
	if hgot := sendRedisCmd(t, rA, wA, "HGET", "svca:sess", "token"); string(hgot.str) != "jwt-secret-value" {
		t.Fatalf("HGET decrypt mismatch: %q", hgot.str)
	}
	_ = sendRedisCmd(t, rA, wA, "LPUSH", "svca:queue", "job-secret-payload")
	if lgot := sendRedisCmd(t, rA, wA, "LPOP", "svca:queue"); string(lgot.str) != "job-secret-payload" {
		t.Fatalf("LPOP decrypt mismatch: %q", lgot.str)
	}

	// Client B (opted-out user choosing NOT to use encryption) stores plaintext normally
	cB, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial opted-out client: %v", err)
	}
	defer cB.Close()
	rB := bufio.NewReader(cB)
	wB := bufio.NewWriter(cB)
	_ = sendRedisCmd(t, rB, wB, "AUTH", "user_optout", "pass")
	_ = sendRedisCmd(t, rB, wB, "SET", "plain:flag", "enabled")
	mockSrv.mu.Lock()
	optOutRaw := mockSrv.kv["plain:flag"]
	mockSrv.mu.Unlock()
	if string(optOutRaw) != "enabled" {
		t.Fatalf("opted-out service should not be forced to encrypt, got %q", optOutRaw)
	}

	// Lock Client A service -> live connection blocked/severed and opted-out Client B stays unaffected!
	if err := mgr.LockService("svc-red-a", "breach lockdown"); err != nil {
		t.Fatalf("LockService: %v", err)
	}
	_ = cA.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, err := writeAndReadCheck(cA); err == nil {
		t.Fatal("expected Client A Redis socket to fail/close after LockService")
	}
	if pong := sendRedisCmd(t, rB, wB, "PING"); string(pong.str) != "PONG" {
		t.Fatalf("opted-out Client B should keep working when Client A locks: %+v", pong)
	}
}

func writeAndReadCheck(c net.Conn) ([]byte, error) {
	_, err := io.WriteString(c, "*1\r\n$4\r\nPING\r\n")
	if err != nil {
		return nil, err
	}
	buf := make([]byte, 128)
	n, err := c.Read(buf)
	if err != nil {
		return nil, err
	}
	if bytes.Contains(buf[:n], []byte("LOCKED")) {
		return nil, io.EOF
	}
	return buf[:n], nil
}

func makeSelfSignedTLSConfig(t *testing.T) *tls.Config {
	t.Helper()
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}
	tmpl := x509.Certificate{
		SerialNumber: big.NewInt(1),
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		DNSNames:     []string{"localhost"},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
	}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &priv.PublicKey, priv)
	if err != nil {
		t.Fatalf("CreateCertificate: %v", err)
	}
	return &tls.Config{
		Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: priv}},
	}
}

func TestPGProxyClientAndUpstreamTLSHandshake(t *testing.T) {
	mgr, router := setupTestController(t)
	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID: "svc-pg-tls",
		ProjectID: "proj-tls",
		DBRoles:   []string{"role_tls"},
	}, true)
	_, _ = mgr.UnlockService("svc-pg-tls", "proj-tls", time.Hour, false)

	srvTLS := makeSelfSignedTLSConfig(t)
	upLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen upstream pg tls: %v", err)
	}
	defer upLn.Close()

	go func() {
		for {
			c, err := upLn.Accept()
			if err != nil {
				return
			}
			go func(conn net.Conn) {
				defer conn.Close()
				var sslReq [8]byte
				if _, err := io.ReadFull(conn, sslReq[:]); err != nil {
					return
				}
				_, _ = conn.Write([]byte{'S'})
				tlsConn := tls.Server(conn, srvTLS)
				if err := tlsConn.Handshake(); err != nil {
					return
				}
				defer tlsConn.Close()
				buf := make([]byte, 4096)
				n, err := tlsConn.Read(buf)
				if err != nil || n == 0 {
					return
				}
				_, _ = tlsConn.Write([]byte{'R', 0, 0, 0, 8, 0, 0, 0, 0})
			}(c)
		}
	}()

	router.Register(ServiceTarget{
		ServiceID:  "svc-pg-tls",
		ProjectID:  "proj-tls",
		DBRoles:    []string{"role_tls"},
		UpstreamPG: upLn.Addr().String(),
		OptedIn:    true,
	})

	proxy, err := NewPGProxy(PGProxyConfig{
		ListenAddr:      "127.0.0.1:0",
		DefaultUpstream: upLn.Addr().String(),
		ClientTLS:       srvTLS,
		UpstreamTLS:     &tls.Config{InsecureSkipVerify: true},
	}, mgr, router)
	if err != nil {
		t.Fatalf("NewPGProxy: %v", err)
	}
	proxyLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen proxy: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.Serve(ctx, proxyLn) }()

	rawClient, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer rawClient.Close()

	var sslReq [8]byte
	binary.BigEndian.PutUint32(sslReq[:4], 8)
	binary.BigEndian.PutUint32(sslReq[4:], pgSSLRequestCode)
	_, _ = rawClient.Write(sslReq[:])
	var sslReply [1]byte
	if _, err := io.ReadFull(rawClient, sslReply[:]); err != nil || sslReply[0] != 'S' {
		t.Fatalf("expected 'S' SSL accept from proxy, got %v %q", err, sslReply[0])
	}
	tlsClient := tls.Client(rawClient, &tls.Config{InsecureSkipVerify: true})
	if err := tlsClient.Handshake(); err != nil {
		t.Fatalf("client TLS handshake failed: %v", err)
	}
	_, _ = tlsClient.Write(encodePGStartup("role_tls", "tlsdb"))
	var authReply [9]byte
	if _, err := io.ReadFull(tlsClient, authReply[:]); err != nil || authReply[0] != 'R' {
		t.Fatalf("expected AuthenticationOk 'R' over TLS, got %v %v", err, authReply)
	}
}

func TestRedisProxyFailedAuthTamperFailClosedAndMultiKey(t *testing.T) {
	mgr, router := setupTestController(t)
	upAddr, mockSrv, cleanupUp := startMockRedis(t)
	defer cleanupUp()

	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-a", ProjectID: "p1"}, true)
	_, _ = mgr.UnlockService("svc-a", "p1", time.Hour, false)
	router.Register(ServiceTarget{
		ServiceID:        "svc-a",
		ProjectID:        "p1",
		RedisUsers:       []string{"user_a"},
		RedisPrefixes:    []string{"svca:"},
		UpstreamRedis:    upAddr,
		OptedIn:          true,
		EncryptRedisVals: true,
	})

	proxy, err := NewRedisProxy(RedisProxyConfig{
		ListenAddr:         "127.0.0.1:0",
		DefaultUpstream:    upAddr,
		TransparentEncrypt: true,
	}, mgr, router)
	if err != nil {
		t.Fatalf("NewRedisProxy: %v", err)
	}
	proxyLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen proxy: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.Serve(ctx, proxyLn) }()

	// 1. Tampered ciphertext fails closed (-ERR LOCKED) instead of leaking ciphertext.
	mockSrv.mu.Lock()
	mockSrv.kv["svca:tampered"] = []byte(crypto.RedisValuePrefix + "!!not-valid-base64!!")
	mockSrv.mu.Unlock()

	c, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer c.Close()
	r := bufio.NewReader(c)
	w := bufio.NewWriter(c)

	if rep := sendRedisCmd(t, r, w, "AUTH", "user_a", "wrongpass"); rep.kind != '-' {
		t.Fatalf("expected -WRONGPASS on failed AUTH, got %+v", rep)
	}
	// Because AUTH failed, unprefixed SET must remain un-encrypted (not bound to svc-a).
	_ = sendRedisCmd(t, r, w, "SET", "public:k", "plain-v")
	mockSrv.mu.Lock()
	rawPub := string(mockSrv.kv["public:k"])
	mockSrv.mu.Unlock()
	if rawPub != "plain-v" {
		t.Fatalf("failed AUTH bound connection to svc-a unexpectedly, stored %q", rawPub)
	}

	// Valid MSET/MGET on prefixed opted-in keys works and encrypts at rest.
	_ = sendRedisCmd(t, r, w, "MSET", "svca:k1", "v1", "svca:k2", "v2")
	mockSrv.mu.Lock()
	if string(mockSrv.kv["svca:k1"]) == "v1" || !bytes.HasPrefix(mockSrv.kv["svca:k1"], []byte(crypto.RedisValuePrefix)) {
		mockSrv.mu.Unlock()
		t.Fatalf("expected MSET svca:k1 encrypted")
	}
	mockSrv.mu.Unlock()
	if rep := sendRedisCmd(t, r, w, "MGET", "svca:k1", "svca:k2"); rep.kind != '*' || len(rep.arr) != 2 || string(rep.arr[0].str) != "v1" || string(rep.arr[1].str) != "v2" {
		t.Fatalf("MGET decrypt mismatch: %+v", rep)
	}

	// Reading tampered GRD1 ciphertext returns Redis error, NOT raw ciphertext.
	if rep := sendRedisCmd(t, r, w, "GET", "svca:tampered"); rep.kind != '-' || !bytes.Contains(rep.str, []byte("LOCKED")) {
		t.Fatalf("expected -ERR LOCKED on tampered ciphertext, got %+v", rep)
	}
}

func TestPGProxyGSSENCNegotiation(t *testing.T) {
	mgr, router := setupTestController(t)
	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID: "svc-gss",
		ProjectID: "proj-gss",
		DBRoles:   []string{"role_gss"},
	}, true)
	_, _ = mgr.UnlockService("svc-gss", "proj-gss", time.Hour, false)

	upLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen upstream pg: %v", err)
	}
	defer upLn.Close()
	go func() {
		for {
			c, err := upLn.Accept()
			if err != nil {
				return
			}
			go func(conn net.Conn) {
				defer conn.Close()
				buf := make([]byte, 4096)
				if n, err := conn.Read(buf); err == nil && n > 0 {
					_, _ = conn.Write([]byte{'R', 0, 0, 0, 8, 0, 0, 0, 0})
				}
			}(c)
		}
	}()

	proxy, err := NewPGProxy(PGProxyConfig{
		ListenAddr:      "127.0.0.1:0",
		DefaultUpstream: upLn.Addr().String(),
	}, mgr, router)
	if err != nil {
		t.Fatalf("NewPGProxy: %v", err)
	}
	proxyLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen proxy: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.Serve(ctx, proxyLn) }()

	client, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer client.Close()

	// Modern libpq sequence: GSSENCRequest -> 'N' -> SSLRequest -> 'N' -> StartupMessage
	var gssReq [8]byte
	binary.BigEndian.PutUint32(gssReq[:4], 8)
	binary.BigEndian.PutUint32(gssReq[4:], pgGSSENCRequestCode)
	_, _ = client.Write(gssReq[:])
	var reply [1]byte
	if _, err := io.ReadFull(client, reply[:]); err != nil || reply[0] != 'N' {
		t.Fatalf("expected 'N' to GSSENCRequest, got %v %q", err, reply[0])
	}

	var sslReq [8]byte
	binary.BigEndian.PutUint32(sslReq[:4], 8)
	binary.BigEndian.PutUint32(sslReq[4:], pgSSLRequestCode)
	_, _ = client.Write(sslReq[:])
	if _, err := io.ReadFull(client, reply[:]); err != nil || reply[0] != 'N' {
		t.Fatalf("expected 'N' to SSLRequest after GSSENC, got %v %q", err, reply[0])
	}

	_, _ = client.Write(encodePGStartup("role_gss", "appdb"))
	var authReply [9]byte
	if _, err := io.ReadFull(client, authReply[:]); err != nil || authReply[0] != 'R' {
		t.Fatalf("expected AuthenticationOk 'R', got %v %v", err, authReply)
	}
}

func TestRedisProxyExtendedCommandsRESP3AndCrossUpstreamGuard(t *testing.T) {
	mgr, router := setupTestController(t)
	defAddr, defMock, cleanupDef := startMockRedis(t)
	defer cleanupDef()
	dedAddr, dedMock, cleanupDed := startMockRedis(t)
	defer cleanupDed()

	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-ext", ProjectID: "p1"}, true)
	_, _ = mgr.UnlockService("svc-ext", "p1", time.Hour, false)
	router.Register(ServiceTarget{
		ServiceID:        "svc-ext",
		ProjectID:        "p1",
		RedisUsers:       []string{"user_ext"},
		RedisPrefixes:    []string{"ext:"},
		UpstreamRedis:    defAddr,
		OptedIn:          true,
		EncryptRedisVals: true,
	})

	_ = mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-ded", ProjectID: "p1"}, true)
	_, _ = mgr.UnlockService("svc-ded", "p1", time.Hour, false)
	router.Register(ServiceTarget{
		ServiceID:        "svc-ded",
		ProjectID:        "p1",
		RedisUsers:       []string{"user_ded"},
		UpstreamRedis:    dedAddr,
		OptedIn:          true,
		EncryptRedisVals: true,
	})

	proxy, err := NewRedisProxy(RedisProxyConfig{
		ListenAddr:         "127.0.0.1:0",
		DefaultUpstream:    defAddr,
		TransparentEncrypt: true,
	}, mgr, router)
	if err != nil {
		t.Fatalf("NewRedisProxy: %v", err)
	}
	proxyLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen proxy: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.Serve(ctx, proxyLn) }()

	c, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer c.Close()
	r := bufio.NewReader(c)
	w := bufio.NewWriter(c)

	if rep := sendRedisCmd(t, r, w, "AUTH", "user_ext", "okpass"); rep.kind != '+' {
		t.Fatalf("expected +OK on AUTH, got %+v", rep)
	}

	// 1. MSETNX encrypts all values at rest and GETEX decrypts cleanly
	if rep := sendRedisCmd(t, r, w, "MSETNX", "ext:nx1", "alpha", "ext:nx2:attr", "beta"); rep.kind != ':' || string(rep.str) != "1" {
		t.Fatalf("expected MSETNX :1, got %+v", rep)
	}
	defMock.mu.Lock()
	if !bytes.HasPrefix(defMock.kv["ext:nx1"], []byte(crypto.RedisValuePrefix)) {
		defMock.mu.Unlock()
		t.Fatal("expected MSETNX value to be GRD1-encrypted on upstream")
	}
	defMock.mu.Unlock()
	if rep := sendRedisCmd(t, r, w, "GETEX", "ext:nx1", "EX", "60"); rep.kind != '$' || string(rep.str) != "alpha" {
		t.Fatalf("expected GETEX decrypted 'alpha', got %+v", rep)
	}

	// 2. RESP3 | attribute prefix on GET reply preserves attributes and decrypts payload
	if rep := sendRedisCmd(t, r, w, "GET", "ext:nx2:attr"); rep.kind != '$' || string(rep.str) != "beta" || len(rep.attrs) != 1 {
		t.Fatalf("expected RESP3 attributed decrypted 'beta', got %+v", rep)
	}

	// 3. SET ... GET encrypts new value and decrypts prior value
	if rep := sendRedisCmd(t, r, w, "SET", "ext:nx1", "gamma", "GET"); rep.kind != '$' || string(rep.str) != "alpha" {
		t.Fatalf("expected SET ... GET prior decrypted 'alpha', got %+v", rep)
	}
	if rep := sendRedisCmd(t, r, w, "GET", "ext:nx1"); rep.kind != '$' || string(rep.str) != "gamma" {
		t.Fatalf("expected GET updated 'gamma', got %+v", rep)
	}

	// 4. Partial-byte mutation APPEND/SETRANGE rejected before corrupting GRD1 ciphertext
	if rep := sendRedisCmd(t, r, w, "APPEND", "ext:nx1", "oops"); rep.kind != '-' || !bytes.Contains(rep.str, []byte("ENCRYPTED_KEY_UNSUPPORTED")) {
		t.Fatalf("expected ENCRYPTED_KEY_UNSUPPORTED on APPEND, got %+v", rep)
	}
	if rep := sendRedisCmd(t, r, w, "GET", "ext:nx1"); rep.kind != '$' || string(rep.str) != "gamma" {
		t.Fatalf("ciphertext altered after rejected APPEND: %+v", rep)
	}

	// 5. Connection that already dialed defAddr via unauthenticated PING cannot late-AUTH onto dedicated dedAddr
	c2, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy c2: %v", err)
	}
	defer c2.Close()
	r2 := bufio.NewReader(c2)
	w2 := bufio.NewWriter(c2)
	if rep := sendRedisCmd(t, r2, w2, "PING"); rep.kind != '+' || string(rep.str) != "PONG" {
		t.Fatalf("expected +PONG on unauthenticated PING, got %+v", rep)
	}
	if rep := sendRedisCmd(t, r2, w2, "AUTH", "user_ded", "okpass"); rep.kind != '-' || !bytes.Contains(rep.str, []byte("CROSS_UPSTREAM_REAUTH")) {
		t.Fatalf("expected CROSS_UPSTREAM_REAUTH when switching upstreams after dial, got %+v", rep)
	}
	dedMock.mu.Lock()
	if len(dedMock.kv) != 0 {
		dedMock.mu.Unlock()
		t.Fatal("expected dedicated upstream never contacted on mismatched late AUTH")
	}
	dedMock.mu.Unlock()

	// 6. Extended encrypted data structures & MULTI/EXEC on prefix-routed unauthenticated connection
	c3, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy c3: %v", err)
	}
	defer c3.Close()
	r3 := bufio.NewReader(c3)
	w3 := bufio.NewWriter(c3)

	// Unauthenticated prefix-routed MSETNX/HSET/HVALS/LPUSHX/LSET/LINDEX/RPOPLPUSH/BLPOP
	_ = sendRedisCmd(t, r3, w3, "HSET", "ext:h", "f1", "hv1")
	if rep := sendRedisCmd(t, r3, w3, "HVALS", "ext:h"); rep.kind != '*' || len(rep.arr) != 1 || string(rep.arr[0].str) != "hv1" {
		t.Fatalf("expected HVALS decrypted 'hv1', got %+v", rep)
	}
	_ = sendRedisCmd(t, r3, w3, "LPUSHX", "ext:l1", "v0")
	_ = sendRedisCmd(t, r3, w3, "LSET", "ext:l1", "0", "v-updated")
	if rep := sendRedisCmd(t, r3, w3, "LINDEX", "ext:l1", "0"); rep.kind != '$' || string(rep.str) != "v-updated" {
		t.Fatalf("expected LINDEX decrypted 'v-updated', got %+v", rep)
	}
	if rep := sendRedisCmd(t, r3, w3, "RPOPLPUSH", "ext:l1", "ext:l2"); rep.kind != '$' || string(rep.str) != "v-updated" {
		t.Fatalf("expected RPOPLPUSH decrypted 'v-updated', got %+v", rep)
	}
	if rep := sendRedisCmd(t, r3, w3, "BLPOP", "ext:l2", "1"); rep.kind != '*' || len(rep.arr) != 2 || string(rep.arr[1].str) != "v-updated" {
		t.Fatalf("expected BLPOP decrypted value 'v-updated', got %+v", rep)
	}

	// Cross-service RENAME must be rejected cleanly
	if rep := sendRedisCmd(t, r3, w3, "RENAME", "ext:nx1", "other:leaked"); rep.kind != '-' {
		t.Fatalf("expected error on cross-service RENAME, got %+v", rep)
	}

	// Prefix-routed MULTI / EXEC encrypts SET and decrypts queued GET & HVALS
	c4, err := net.DialTimeout("tcp", proxyLn.Addr().String(), 3*time.Second)
	if err != nil {
		t.Fatalf("dial proxy c4: %v", err)
	}
	defer c4.Close()
	r4 := bufio.NewReader(c4)
	w4 := bufio.NewWriter(c4)
	if rep := sendRedisCmd(t, r4, w4, "MULTI"); rep.kind != '+' || string(rep.str) != "OK" {
		t.Fatalf("MULTI failed: %+v", rep)
	}
	if rep := sendRedisCmd(t, r4, w4, "SET", "ext:tx", "tx-secret"); rep.kind != '+' || string(rep.str) != "QUEUED" {
		t.Fatalf("expected +QUEUED for SET, got %+v", rep)
	}
	if rep := sendRedisCmd(t, r4, w4, "GET", "ext:tx"); rep.kind != '+' || string(rep.str) != "QUEUED" {
		t.Fatalf("expected +QUEUED for GET, got %+v", rep)
	}
	if rep := sendRedisCmd(t, r4, w4, "HVALS", "ext:h"); rep.kind != '+' || string(rep.str) != "QUEUED" {
		t.Fatalf("expected +QUEUED for HVALS, got %+v", rep)
	}
	execRep := sendRedisCmd(t, r4, w4, "EXEC")
	if execRep.kind != '*' || len(execRep.arr) != 3 || string(execRep.arr[1].str) != "tx-secret" || string(execRep.arr[2].arr[0].str) != "hv1" {
		t.Fatalf("expected EXEC to decrypt queued GET & HVALS, got %+v", execRep)
	}
}
