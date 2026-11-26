// Command edge-sidecar is the SMSLY data-plane edge helper.
//
// It answers two questions WITHOUT the Django control plane:
//
//	GET /ask?domain=<host>   — Caddy on_demand_tls `ask` replacement.
//	                           200 when the domain is allow-listed, 404 otherwise.
//	GET /auth-verify          — Traefik forwardAuth replacement.
//	                           200 + X-User-Id/X-Edge-Scope on valid edge JWT, 401 otherwise.
//
// Design rules (AGENTS.md: Django outage must not break TLS/routing):
//   - stdlib only (net/http, crypto/hmac, encoding/json, ...). No deps.
//   - allow-list is a JSON file Django writes atomically next to the
//     Caddyfile; the sidecar watches it with mtime polling and hot-reloads.
//   - JWTs are verified locally with EDGE_JWT_SECRET (HS256, exp check).
//     Legacy DRF/APIToken fallback is NOT supported at the edge — those
//     need Django by design. Edge JWT is the only gate token.
//   - fail-closed everywhere: missing secret/file/token => deny.
//   - listen is 0.0.0.0:8971 by default, NOT loopback: Caddy and Traefik
//     run as Docker containers and reach the sidecar via
//     host.docker.internal:host-gateway, which arrives on the bridge
//     gateway IP — a 127.0.0.1 bind would refuse them. The port is
//     unauthenticated only for /health ("ok"); /ask is gated by
//     CADDY_ASK_SECRET (fail-closed when unset) and /auth-verify needs a
//     valid edge JWT. Do not publish this port in compose or open it in UFW.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

type config struct {
	listen      string
	allowFile   string
	secretFile  string
	askSecret   string
	pollSecs    int
	readTimeout time.Duration
}

type allowList struct {
	mu      sync.RWMutex
	domains map[string]bool
	mtime   time.Time
	size    int64
	path    string
}

func (a *allowList) reloadIfChanged() {
	fi, err := os.Stat(a.path)
	if err != nil {
		return
	}
	mt := fi.ModTime()
	sz := fi.Size()
	a.mu.RLock()
	cur := a.mtime
	curSize := a.size
	a.mu.RUnlock()
	if mt.Equal(cur) && sz == curSize {
		return
	}
	raw, err := os.ReadFile(a.path)
	if err != nil {
		log.Printf("allow-list read error (%s): %v", a.path, err)
		return
	}
	var payload struct {
		Domains []string `json:"domains"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		log.Printf("allow-list JSON parse error (%s): %v", a.path, err)
		return
	}
	next := make(map[string]bool, len(payload.Domains))
	for _, d := range payload.Domains {
		d = normalizeHost(d)
		if d != "" {
			next[d] = true
		}
	}
	a.mu.Lock()
	a.domains = next
	a.mtime = mt
	a.size = sz
	a.mu.Unlock()
}

func normalizeHost(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return ""
	}
	if idx := strings.IndexByte(s, ','); idx >= 0 {
		s = strings.TrimSpace(s[:idx])
	}
	if strings.Contains(s, ":") {
		if host, _, err := net.SplitHostPort(s); err == nil {
			s = host
		} else {
			s = strings.TrimPrefix(strings.TrimSuffix(s, "]"), "[")
		}
	}
	return strings.ToLower(strings.TrimSpace(strings.TrimSuffix(s, ".")))
}

func (a *allowList) allowed(host string) bool {
	host = normalizeHost(host)
	if host == "" {
		return false
	}
	a.mu.RLock()
	defer a.mu.RUnlock()
	if a.domains[host] {
		return true
	}
	// Wildcard entries are stored as "*.example.com" (single-label wildcard per TLS RFC).
	labels := strings.Split(host, ".")
	if len(labels) >= 2 && a.domains["*."+strings.Join(labels[1:], ".")] {
		return true
	}
	return false
}

type secretHolder struct {
	mu     sync.RWMutex
	secret []byte
	mtime  time.Time
	size   int64
	path   string
}

func (s *secretHolder) reloadIfChanged() {
	if s.path == "" {
		return
	}
	fi, err := os.Stat(s.path)
	if err != nil {
		return
	}
	mt := fi.ModTime()
	sz := fi.Size()
	s.mu.RLock()
	cur := s.mtime
	curSize := s.size
	s.mu.RUnlock()
	if mt.Equal(cur) && sz == curSize {
		return
	}
	raw, err := os.ReadFile(s.path)
	if err != nil {
		return
	}
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" {
		return
	}
	s.mu.Lock()
	s.secret = []byte(trimmed)
	s.mtime = mt
	s.size = sz
	s.mu.Unlock()
}

func (s *secretHolder) get() []byte {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.secret
}

func askHandler(al *allowList, askSecret string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		// Fail closed: an unset secret would turn /ask into an
		// unauthenticated domain-allowlist oracle. Caddy treats any
		// non-2xx `ask` answer as "do not issue", so on-demand TLS
		// issuance pauses until CADDY_ASK_SECRET is configured —
		// matching the fail-closed rule of /auth-verify.
		if askSecret == "" {
			log.Println("ask refused: CADDY_ASK_SECRET not configured (fail-closed)")
			http.Error(w, "unavailable", http.StatusServiceUnavailable)
			return
		}
		got := r.URL.Query().Get("secret")
		if got == "" {
			got = r.Header.Get("X-Caddy-Secret")
		}
		gotSum := sha256.Sum256([]byte(got))
		expSum := sha256.Sum256([]byte(askSecret))
		if subtle.ConstantTimeCompare(gotSum[:], expSum[:]) != 1 {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		domain := normalizeHost(r.URL.Query().Get("domain"))
		if domain == "" {
			http.Error(w, "missing domain", http.StatusBadRequest)
			return
		}
		if al.allowed(domain) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("ok"))
			return
		}
		http.Error(w, "not allowed", http.StatusNotFound)
	}
}

func parseJWT(token string) (headerB64, payloadB64, sig []byte, err error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, nil, nil, fmt.Errorf("bad token shape")
	}
	h, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, nil, nil, err
	}
	p, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, nil, nil, err
	}
	s, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return nil, nil, nil, err
	}
	return h, p, s, nil
}

func authVerifyHandler(sec *secretHolder) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		secret := sec.get()
		if len(secret) == 0 {
			http.Error(w, "unavailable", http.StatusServiceUnavailable)
			return
		}
		auth := strings.TrimSpace(r.Header.Get("Authorization"))
		if auth == "" || len(auth) > 8192 {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		token := auth
		if strings.HasPrefix(strings.ToLower(token), "bearer ") {
			token = strings.TrimSpace(token[7:])
		}
		hdrRaw, payloadRaw, sig, err := parseJWT(token)
		if err != nil {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		var hdr struct {
			Alg string `json:"alg"`
			Typ string `json:"typ"`
		}
		if err := json.Unmarshal(hdrRaw, &hdr); err != nil || !strings.EqualFold(hdr.Alg, "HS256") {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		mac := hmac.New(sha256.New, secret)
		parts := strings.SplitN(token, ".", 3)
		mac.Write([]byte(parts[0] + "." + parts[1]))
		if subtle.ConstantTimeCompare(mac.Sum(nil), sig) != 1 {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		var claims struct {
			Sub   string `json:"sub"`
			Scope string `json:"scope"`
			Aud   string `json:"aud"`
			Exp   int64  `json:"exp"`
			Jti   string `json:"jti"`
		}
		if err := json.Unmarshal(payloadRaw, &claims); err != nil {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if claims.Sub == "" || claims.Jti == "" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if claims.Exp <= 0 || time.Now().Unix() > claims.Exp {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if claims.Aud == "" && claims.Scope != "admin" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if claims.Aud != "" {
			audHost := normalizeHost(claims.Aud)
			reqHost := normalizeHost(r.Header.Get("X-Forwarded-Host"))
			if reqHost == "" || audHost == "" || reqHost != audHost {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
		}
		scope := claims.Scope
		if scope == "" {
			scope = "service"
		}
		w.Header().Set("X-User-Id", claims.Sub)
		w.Header().Set("X-Edge-Scope", scope)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func getenv(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

func main() {
	var cfg config
	flag.StringVar(&cfg.listen, "listen", getenv("EDGE_SIDECAR_LISTEN", "0.0.0.0:8971"), "listen address (0.0.0.0: containers arrive via bridge gateway IP)")
	flag.StringVar(&cfg.allowFile, "allow-file", getenv("EDGE_SIDECAR_ALLOW_FILE", "/opt/smsly-hosting/caddy-config/.tls-allow-list.json"), "allow-list JSON path")
	flag.StringVar(&cfg.secretFile, "secret-file", getenv("EDGE_JWT_SECRET_FILE", ""), "file holding EDGE_JWT_SECRET")
	flag.IntVar(&cfg.pollSecs, "poll-secs", 5, "allow-list/secret poll interval seconds")
	flag.DurationVar(&cfg.readTimeout, "read-timeout", 5*time.Second, "HTTP server read timeout")
	flag.Parse()
	if cfg.readTimeout <= 0 {
		cfg.readTimeout = 5 * time.Second
	}
	cfg.askSecret = strings.TrimSpace(os.Getenv("CADDY_ASK_SECRET"))
	if cfg.askSecret == "" {
		log.Println("WARNING: CADDY_ASK_SECRET is not configured — /ask refuses all " +
			"on-demand TLS requests (fail-closed). Caddy cert issuance for new " +
			"domains will pause until the secret is set.")
	}

	al := &allowList{domains: map[string]bool{}, path: cfg.allowFile}
	al.reloadIfChanged()
	sec := &secretHolder{path: cfg.secretFile}
	if cfg.secretFile == "" {
		if v := strings.TrimSpace(os.Getenv("EDGE_JWT_SECRET")); v != "" {
			sec.secret = []byte(v)
		}
	} else {
		sec.reloadIfChanged()
		if len(sec.get()) == 0 {
			if v := strings.TrimSpace(os.Getenv("EDGE_JWT_SECRET")); v != "" {
				sec.mu.Lock()
				sec.secret = []byte(v)
				sec.mu.Unlock()
			}
		}
	}

	if cfg.pollSecs < 1 {
		cfg.pollSecs = 5
	}
	go func() {
		t := time.NewTicker(time.Duration(cfg.pollSecs) * time.Second)
		defer t.Stop()
		for range t.C {
			al.reloadIfChanged()
			sec.reloadIfChanged()
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/ask", askHandler(al, cfg.askSecret))
	mux.HandleFunc("/auth-verify", authVerifyHandler(sec))
	mux.HandleFunc("/health", healthHandler)

	srv := &http.Server{
		Addr:              cfg.listen,
		Handler:           mux,
		ReadTimeout:       cfg.readTimeout,
		ReadHeaderTimeout: 3 * time.Second,
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Printf("edge-sidecar listening on %s (allow=%s)", cfg.listen, cfg.allowFile)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("listen: %v", err)
	}
}
