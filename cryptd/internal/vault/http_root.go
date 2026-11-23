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
	AllowedNodes   map[string]bool
	MaxClockSkew   time.Duration
	AllowPastWeeks uint64
	PinnedSalt     string
	StateFile      string
}

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
	if cfg.HMACSecret == "" && cfg.AuthToken == "" {
		return nil, errors.New("root vault requires HMACSecret or AuthToken")
	}
	if cfg.MaxClockSkew <= 0 {
		cfg.MaxClockSkew = 2 * time.Minute
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

func (s *RootVaultServer) handleDeriveSMK(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 64<<10))
	if err != nil {
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
	salt := req.ClusterSalt
	if strings.TrimSpace(s.cfg.PinnedSalt) != "" {
		salt = strings.TrimSpace(s.cfg.PinnedSalt)
	}

	s.mu.Lock()
	changedHighest := false
	if req.Week > s.highestWeek {
		s.highestWeek = req.Week
		changedHighest = true
	}
	if s.highestWeek > s.cfg.AllowPastWeeks && req.Week < s.highestWeek-s.cfg.AllowPastWeeks {
		s.mu.Unlock()
		http.Error(w, "requested week violates rollback floor", http.StatusConflict)
		return
	}
	if changedHighest && s.cfg.StateFile != "" {
		payload, _ := json.Marshal(struct {
			HighestWeek uint64 `json:"highest_week"`
		}{HighestWeek: s.highestWeek})
		_ = os.WriteFile(s.cfg.StateFile, payload, 0o600)
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

func (s *RootVaultServer) authenticate(r *http.Request, body []byte) bool {
	if s.cfg.AuthToken != "" {
		hdr := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(hdr), []byte(s.cfg.AuthToken)) != 1 {
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
		if subtle.ConstantTimeCompare([]byte(sig), []byte(expected)) != 1 {
			return false
		}
		s.mu.Lock()
		for k, exp := range s.seenNonces {
			if exp < now-skew {
				delete(s.seenNonces, k)
			}
		}
		if _, replay := s.seenNonces[nonce]; replay {
			s.mu.Unlock()
			return false
		}
		s.seenNonces[nonce] = ts
		s.mu.Unlock()
	}
	return true
}
