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
	ListenAddr      string
	DefaultUpstream string
	DialTimeout     time.Duration
	ClientTLS       *tls.Config
	UpstreamTLS     *tls.Config
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
		// Best-effort forward query cancel packet to default upstream unless vault is locked.
		if p.epochs.Status().State != epoch.StateLocked {
			upstreamAddr := p.cfg.DefaultUpstream
			if upstreamAddr != "" {
				if up, derr := (&net.Dialer{Timeout: p.cfg.DialTimeout}).DialContext(ctx, "tcp", upstreamAddr); derr == nil {
					_, _ = up.Write(startupFrame)
					_ = up.Close()
				}
			}
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
		tlsUpstream, uerr := negotiateUpstreamTLS(upstream, p.cfg.UpstreamTLS)
		if uerr != nil {
			_ = writePGErrorResponse(client, "08006", fmt.Sprintf("trulay-grid: upstream TLS failed: %v", uerr))
			return
		}
		upstream = tlsUpstream
		defer upstream.Close()
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

	if _, err := upstream.Write(startupFrame); err != nil {
		return
	}

	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(upstream, client)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(client, upstream)
		done <- struct{}{}
	}()
	<-done
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
					return nil, nil, nil, false, werr
				}
				tlsConn := tls.Server(cur, p.cfg.ClientTLS)
				if herr := tlsConn.Handshake(); herr != nil {
					_ = tlsConn.Close()
					return nil, nil, nil, false, herr
				}
				cur = tlsConn
			} else {
				if _, werr := cur.Write([]byte{'N'}); werr != nil {
					return nil, nil, nil, false, werr
				}
			}
			continue
		case pgGSSENCRequestCode:
			// Decline GSSAPI encryption so modern libpq/psycopg/asyncpg clients
			// proceed cleanly to SSLRequest or plain StartupMessage.
			_, _ = cur.Write([]byte{'N'})
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

func negotiateUpstreamTLS(conn net.Conn, cfg *tls.Config) (net.Conn, error) {
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
		return nil, errors.New("upstream Postgres refused TLS negotiation")
	}
	tlsConn := tls.Client(conn, cfg)
	if err := tlsConn.Handshake(); err != nil {
		_ = tlsConn.Close()
		return nil, err
	}
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
