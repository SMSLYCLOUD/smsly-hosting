package vault

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
)

type smkFetchRequest struct {
	Week        uint64 `json:"week"`
	ClusterSalt string `json:"cluster_salt"`
	NodeID      string `json:"node_id"`
}

type smkFetchResponse struct {
	Week       uint64 `json:"week"`
	SMKB64     string `json:"smk_b64"`
	KeyVersion string `json:"key_version"`
	DerivedAt  string `json:"derived_at"`
}

// HTTPRootVaultProvider fetches pre-derived weekly SMKs from an external Go Root Vault.
// The Master Key never leaves the external Root Vault host.
type HTTPRootVaultProvider struct {
	cfg    Config
	client *http.Client
}

// NewHTTPRootVaultProvider validates config and builds the http_vault provider.
func NewHTTPRootVaultProvider(cfg Config) (*HTTPRootVaultProvider, error) {
	if strings.TrimSpace(cfg.Endpoint) == "" {
		return nil, errors.New("http_vault requires CRYPTD_VAULT_ENDPOINT")
	}
	if cfg.HMACSecret == "" && cfg.AuthToken == "" && (cfg.ClientCertFile == "" || cfg.ClientKeyFile == "") {
		return nil, errors.New("http_vault requires HMACSecret, AuthToken, or mTLS client certificate")
	}
	client, err := BuildHTTPClient(cfg)
	if err != nil {
		return nil, err
	}
	if cfg.NodeID == "" {
		cfg.NodeID = "grid-paas-node"
	}
	return &HTTPRootVaultProvider{cfg: cfg, client: client}, nil
}

func (p *HTTPRootVaultProvider) Name() string {
	return "http_vault"
}

func signVaultRequest(secret, method, path, ts, nonce string, body []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(method + "\n" + path + "\n" + ts + "\n" + nonce + "\n"))
	bodyHash := sha256.Sum256(body)
	_, _ = mac.Write([]byte(hex.EncodeToString(bodyHash[:])))
	return hex.EncodeToString(mac.Sum(nil))
}

func (p *HTTPRootVaultProvider) FetchWeeklySMK(ctx context.Context, week uint64, clusterSalt string) (*SMKResult, error) {
	payload, err := json.Marshal(smkFetchRequest{
		Week:        week,
		ClusterSalt: clusterSalt,
		NodeID:      p.cfg.NodeID,
	})
	if err != nil {
		return nil, err
	}
	endpoint := strings.TrimRight(p.cfg.Endpoint, "/") + "/v1/smk"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Grid-Node-Id", p.cfg.NodeID)
	if p.cfg.AuthToken != "" {
		req.Header.Set("Authorization", "Bearer "+p.cfg.AuthToken)
	}
	if p.cfg.HMACSecret != "" {
		ts := strconv.FormatInt(time.Now().Unix(), 10)
		nonceBytes := make([]byte, 12)
		_, _ = io.ReadFull(rand.Reader, nonceBytes)
		nonce := hex.EncodeToString(nonceBytes)
		sig := signVaultRequest(p.cfg.HMACSecret, http.MethodPost, "/v1/smk", ts, nonce, payload)
		req.Header.Set("X-Grid-Timestamp", ts)
		req.Header.Set("X-Grid-Nonce", nonce)
		req.Header.Set("X-Grid-Signature", sig)
	}

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("http_vault request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("http_vault returned HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(respBody)))
	}

	var parsed smkFetchResponse
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		return nil, fmt.Errorf("decode http_vault response: %w", err)
	}
	if parsed.Week != week {
		return nil, fmt.Errorf("http_vault week mismatch: requested %d got %d", week, parsed.Week)
	}
	smk, err := base64.StdEncoding.DecodeString(parsed.SMKB64)
	if err != nil || len(smk) != crypto.KeySize {
		return nil, errors.New("http_vault returned invalid SMK payload")
	}
	ver := parsed.KeyVersion
	if ver == "" {
		ver = fmt.Sprintf("w%d", week)
	}
	return &SMKResult{
		Week:       week,
		SMK:        smk,
		Provider:   p.Name(),
		KeyVersion: ver,
		FetchedAt:  time.Now().UTC(),
	}, nil
}

func (p *HTTPRootVaultProvider) Health(ctx context.Context) error {
	endpoint := strings.TrimRight(p.cfg.Endpoint, "/") + "/health"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("http_vault health status %d", resp.StatusCode)
	}
	return nil
}

// RootVaultConfig configures the external standalone Root Vault server (-mode=root-vault).
type RootVaultConfig struct {
	MasterKey      []byte
	KeyVersion     string
	HMACSecret     string
	AuthToken      string
	ClientCA       string
	AllowedNodes   map[string]bool
	MaxClockSkew   time.Duration
	AllowPastWeeks uint64
	PinnedSalt     string
	StateFile      string
	EpochAnchor    time.Time
	EpochDuration  time.Duration
}

const maxRootVaultSeenNonces = 65536

// RootVaultServer holds ONLY the root Master Key on an external vault host and serves weekly SMKs.
// It never exports the Master Key itself.
type RootVaultServer struct {
	mu           sync.Mutex
	cfg          RootVaultConfig
	highestWeek  uint64
	seenNonces   map[string]int64
}

// NewRootVaultHandler constructs an HTTP handler for the external Go Root Vault server.
func NewRootVaultHandler(cfg RootVaultConfig) (http.Handler, error) {
	if len(cfg.MasterKey) < 16 {
		return nil, errors.New("root vault MasterKey must be at least 16 bytes")
	}
	if cfg.HMACSecret == "" && cfg.AuthToken == "" && strings.TrimSpace(cfg.ClientCA) == "" {
		return nil, errors.New("root vault requires HMACSecret, AuthToken, or ClientCA")
	}
	if cfg.MaxClockSkew <= 0 {
		cfg.MaxClockSkew = 2 * time.Minute
	}
	if cfg.EpochAnchor.IsZero() {
		cfg.EpochAnchor = time.Date(2025, 1, 6, 0, 0, 0, 0, time.UTC)
	}
	if cfg.EpochDuration <= 0 {
		cfg.EpochDuration = 7 * 24 * time.Hour
	}
	if cfg.KeyVersion == "" {
		cfg.KeyVersion = "mk-v1"
	}
	mkCopy := make([]byte, len(cfg.MasterKey))
	copy(mkCopy, cfg.MasterKey)
	cfg.MasterKey = mkCopy

	rv := &RootVaultServer{
		cfg:        cfg,
		seenNonces: make(map[string]int64),
	}
	if cfg.StateFile != "" {
		if raw, err := os.ReadFile(cfg.StateFile); err == nil {
			var st struct {
				HighestWeek uint64 `json:"highest_week"`
			}
			if json.Unmarshal(raw, &st) == nil {
				rv.highestWeek = st.HighestWeek
			}
		}
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"role":"root-vault"}`))
	})
	mux.HandleFunc("/v1/smk", rv.handleDeriveSMK)
	return mux, nil
}

func writeRootVaultStateAtomic(path string, payload []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".state-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	cleanup := func() {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
	}
	if _, err := tmp.Write(payload); err != nil {
		cleanup()
		return err
	}
	if err := tmp.Chmod(0o600); err != nil {
		cleanup()
		return err
	}
	if err := tmp.Sync(); err != nil {
		cleanup()
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	return os.Rename(tmpName, path)
}

func (s *RootVaultServer) handleDeriveSMK(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !s.authenticateHeaders(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	defer r.Body.Close()
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "bad request body", http.StatusBadRequest)
		return
	}
	if !s.authenticate(r, body) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	var req smkFetchRequest
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}
	if len(s.cfg.AllowedNodes) > 0 && !s.cfg.AllowedNodes[req.NodeID] {
		http.Error(w, "node not authorized", http.StatusForbidden)
		return
	}
	if ok, tooMany := s.recordNonce(r); !ok {
		if tooMany {
			http.Error(w, "too many requests", http.StatusTooManyRequests)
		} else {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
		}
		return
	}
	nowUnix := time.Now().Unix()
	if nowUnix < 0 {
		nowUnix = 0
	}
	wallWeek := uint64(nowUnix / (7 * 86400))
	if !s.cfg.EpochAnchor.IsZero() {
		dur := s.cfg.EpochDuration
		if dur <= 0 {
			dur = 7 * 24 * time.Hour
		}
		now := time.Now().UTC()
		if now.Before(s.cfg.EpochAnchor) {
			wallWeek = 1
		} else {
			wallWeek = uint64(now.Sub(s.cfg.EpochAnchor)/dur) + 1
		}
	}
	if req.Week == 0 || req.Week > wallWeek+1 {
		http.Error(w, "requested week out of bounds", http.StatusBadRequest)
		return
	}
	salt := req.ClusterSalt
	if strings.TrimSpace(s.cfg.PinnedSalt) != "" {
		salt = strings.TrimSpace(s.cfg.PinnedSalt)
	}

	s.mu.Lock()
	if s.highestWeek > s.cfg.AllowPastWeeks && req.Week < s.highestWeek-s.cfg.AllowPastWeeks {
		s.mu.Unlock()
		http.Error(w, "requested week violates rollback floor", http.StatusConflict)
		return
	}
	changedHighest := false
	if req.Week > s.highestWeek {
		s.highestWeek = req.Week
		changedHighest = true
	}
	if changedHighest && s.cfg.StateFile != "" {
		payload, _ := json.Marshal(struct {
			HighestWeek uint64 `json:"highest_week"`
		}{HighestWeek: s.highestWeek})
		_ = writeRootVaultStateAtomic(s.cfg.StateFile, payload)
	}
	s.mu.Unlock()

	smk, err := crypto.DeriveWeeklySMK(s.cfg.MasterKey, req.Week, salt)
	if err != nil {
		http.Error(w, "derivation failed", http.StatusInternalServerError)
		return
	}
	defer crypto.ZeroBytes(smk)

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(smkFetchResponse{
		Week:       req.Week,
		SMKB64:     base64.StdEncoding.EncodeToString(smk),
		KeyVersion: s.cfg.KeyVersion,
		DerivedAt:  time.Now().UTC().Format(time.RFC3339),
	})
}

// authenticateHeaders validates Bearer token and required HMAC header fields/clock-skew/replay
// before reading the HTTP request body, preventing unauthenticated slow-body connection exhaustion.
func (s *RootVaultServer) authenticateHeaders(r *http.Request) bool {
	if strings.TrimSpace(s.cfg.ClientCA) != "" {
		if r.TLS == nil || len(r.TLS.VerifiedChains) == 0 {
			return false
		}
	}
	if s.cfg.AuthToken != "" {
		hdr := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		hdrSum := sha256.Sum256([]byte(hdr))
		tokSum := sha256.Sum256([]byte(s.cfg.AuthToken))
		if subtle.ConstantTimeCompare(hdrSum[:], tokSum[:]) != 1 {
			return false
		}
	}
	if s.cfg.HMACSecret != "" {
		tsStr := r.Header.Get("X-Grid-Timestamp")
		nonce := r.Header.Get("X-Grid-Nonce")
		sig := r.Header.Get("X-Grid-Signature")
		ts, err := strconv.ParseInt(tsStr, 10, 64)
		if err != nil || nonce == "" || sig == "" {
			return false
		}
		now := time.Now().Unix()
		skew := int64(s.cfg.MaxClockSkew.Seconds())
		if ts < now-skew || ts > now+skew {
			return false
		}
		s.mu.Lock()
		_, replay := s.seenNonces[nonce]
		s.mu.Unlock()
		if replay {
			return false
		}
	}
	return true
}

func (s *RootVaultServer) authenticate(r *http.Request, body []byte) bool {
	if strings.TrimSpace(s.cfg.ClientCA) != "" {
		if r.TLS == nil || len(r.TLS.VerifiedChains) == 0 {
			return false
		}
	}
	if s.cfg.AuthToken != "" {
		hdr := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		hdrSum := sha256.Sum256([]byte(hdr))
		tokSum := sha256.Sum256([]byte(s.cfg.AuthToken))
		if subtle.ConstantTimeCompare(hdrSum[:], tokSum[:]) != 1 {
			return false
		}
	}
	if s.cfg.HMACSecret != "" {
		tsStr := r.Header.Get("X-Grid-Timestamp")
		nonce := r.Header.Get("X-Grid-Nonce")
		sig := r.Header.Get("X-Grid-Signature")
		ts, err := strconv.ParseInt(tsStr, 10, 64)
		if err != nil || nonce == "" || sig == "" {
			return false
		}
		now := time.Now().Unix()
		skew := int64(s.cfg.MaxClockSkew.Seconds())
		if ts < now-skew || ts > now+skew {
			return false
		}
		expected := signVaultRequest(s.cfg.HMACSecret, r.Method, r.URL.Path, tsStr, nonce, body)
		sigSum := sha256.Sum256([]byte(sig))
		expSum := sha256.Sum256([]byte(expected))
		if subtle.ConstantTimeCompare(sigSum[:], expSum[:]) != 1 {
			return false
		}
		s.mu.Lock()
		for k, exp := range s.seenNonces {
			if exp < now-skew {
				delete(s.seenNonces, k)
			}
		}
		_, replay := s.seenNonces[nonce]
		s.mu.Unlock()
		if replay {
			return false
		}
	}
	return true
}

func (s *RootVaultServer) recordNonce(r *http.Request) (ok bool, tooMany bool) {
	if s.cfg.HMACSecret == "" {
		return true, false
	}
	tsStr := r.Header.Get("X-Grid-Timestamp")
	nonce := r.Header.Get("X-Grid-Nonce")
	ts, err := strconv.ParseInt(tsStr, 10, 64)
	if err != nil || nonce == "" {
		return false, false
	}
	now := time.Now().Unix()
	skew := int64(s.cfg.MaxClockSkew.Seconds())
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, exp := range s.seenNonces {
		if exp < now-skew {
			delete(s.seenNonces, k)
		}
	}
	if _, replay := s.seenNonces[nonce]; replay {
		return false, false
	}
	if len(s.seenNonces) >= maxRootVaultSeenNonces {
		return false, true
	}
	s.seenNonces[nonce] = ts
	return true, false
}
