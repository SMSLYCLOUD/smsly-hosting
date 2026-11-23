package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/dbproxy"
	"smsly-hosting/cryptd/internal/epoch"
	"smsly-hosting/cryptd/internal/vault"
	"smsly-hosting/cryptd/internal/volumes"
)

func newTestServer(t *testing.T) (*Server, *epoch.Manager) {
	t.Helper()
	tmp := t.TempDir()
	store, err := crypto.OpenKeyStore(filepath.Join(tmp, "ks"))
	if err != nil {
		t.Fatalf("OpenKeyStore: %v", err)
	}
	prov, err := vault.NewProvider(vault.Config{
		ProviderName:  "dev_memory",
		AllowDevVault: true,
		DevMasterKey:  strings.Repeat("cd", 32),
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
	router := dbproxy.NewRouter(dbproxy.ServiceTarget{ServiceID: crypto.ControlPlaneServiceID})
	volMgr, err := volumes.NewManager(volumes.Config{
		BaseDir:   filepath.Join(tmp, "vols"),
		Backend:   "mock",
		AllowMock: true,
	}, mgr, store, nil)
	if err != nil {
		t.Fatalf("volumes.NewManager: %v", err)
	}
	srv, err := NewServer("secret-hdr-tok", mgr, volMgr, router)
	if err != nil {
		t.Fatalf("NewServer: %v", err)
	}
	return srv, mgr
}

func doJSONReq(t *testing.T, h http.Handler, method, path, secret string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var buf bytes.Buffer
	if body != nil {
		if err := json.NewEncoder(&buf).Encode(body); err != nil {
			t.Fatalf("encode req: %v", err)
		}
	}
	req := httptest.NewRequest(method, path, &buf)
	if secret != "" {
		req.Header.Set("X-Cryptd-Secret", secret)
	}
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	return rr
}

func TestHTTPAPIAuthOptInTransitVolumesAndLockdown(t *testing.T) {
	srv, _ := newTestServer(t)

	// 1. Unauthorized rejected
	if rr := doJSONReq(t, srv, http.MethodGet, "/v1/status", "wrong-secret", nil); rr.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for wrong secret, got %d", rr.Code)
	}

	// 2. Register opted-out service -> transit encrypt with allow_opt_out skips cleanly!
	rr := doJSONReq(t, srv, http.MethodPost, "/v1/services/register", "secret-hdr-tok", map[string]any{
		"service_id": "svc-optout",
		"project_id": "p1",
		"opted_in":   false,
	})
	if rr.Code != http.StatusOK {
		t.Fatalf("register optout failed: %d %s", rr.Code, rr.Body.String())
	}
	rr = doJSONReq(t, srv, http.MethodPost, "/v1/transit/encrypt", "secret-hdr-tok", map[string]any{
		"service_id":    "svc-optout",
		"plaintext_b64": base64.StdEncoding.EncodeToString([]byte("plain")),
		"allow_opt_out": true,
	})
	var skipResp map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &skipResp)
	if skipResp["skipped"] != true {
		t.Fatalf("expected allow_opt_out to skip encryption for opted-out service: %+v", skipResp)
	}

	// 3. Register opted-in service -> transit roundtrip + backup key wrap/unwrap + zero-DEK-exposure volume
	_ = doJSONReq(t, srv, http.MethodPost, "/v1/services/register", "secret-hdr-tok", map[string]any{
		"service_id": "svc-sec",
		"project_id": "p1",
		"opted_in":   true,
		"unlock_now": true,
	})
	encRR := doJSONReq(t, srv, http.MethodPost, "/v1/transit/encrypt", "secret-hdr-tok", map[string]any{
		"service_id":    "svc-sec",
		"project_id":    "p1",
		"context":       "env",
		"mode":          "persistent",
		"plaintext_b64": base64.StdEncoding.EncodeToString([]byte("db-password-secret")),
	})
	if encRR.Code != http.StatusOK {
		t.Fatalf("transit encrypt failed: %d %s", encRR.Code, encRR.Body.String())
	}
	var encOut struct {
		Token string `json:"token"`
	}
	_ = json.Unmarshal(encRR.Body.Bytes(), &encOut)
	if !strings.HasPrefix(encOut.Token, "grid:v1:") {
		t.Fatalf("unexpected token: %q", encOut.Token)
	}

	decRR := doJSONReq(t, srv, http.MethodPost, "/v1/transit/decrypt", "secret-hdr-tok", map[string]any{
		"service_id": "svc-sec",
		"project_id": "p1",
		"context":    "env",
		"token":      encOut.Token,
	})
	var decOut struct {
		PlaintextB64 string `json:"plaintext_b64"`
	}
	_ = json.Unmarshal(decRR.Body.Bytes(), &decOut)
	pt, _ := base64.StdEncoding.DecodeString(decOut.PlaintextB64)
	if string(pt) != "db-password-secret" {
		t.Fatalf("transit decrypt mismatch: %q", pt)
	}

	// Backup wrap/unwrap
	dekB64 := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEE}, 32))
	wrapRR := doJSONReq(t, srv, http.MethodPost, "/v1/backups/wrap-key", "secret-hdr-tok", map[string]any{
		"service_id": "svc-sec",
		"project_id": "p1",
		"dek_b64":    dekB64,
	})
	var wrapOut struct {
		WrappedToken string `json:"wrapped_token"`
	}
	_ = json.Unmarshal(wrapRR.Body.Bytes(), &wrapOut)
	unwrapRR := doJSONReq(t, srv, http.MethodPost, "/v1/backups/unwrap-key", "secret-hdr-tok", map[string]any{
		"service_id":    "svc-sec",
		"project_id":    "p1",
		"wrapped_token": wrapOut.WrappedToken,
	})
	var unwrapOut struct {
		DEKB64 string `json:"dek_b64"`
	}
	_ = json.Unmarshal(unwrapRR.Body.Bytes(), &unwrapOut)
	if unwrapOut.DEKB64 != dekB64 {
		t.Fatalf("backup unwrap mismatch")
	}

	// Volume provision without dek_hex (daemon-generated DEK; zero exposure)
	volRR := doJSONReq(t, srv, http.MethodPost, "/volumes/provision", "secret-hdr-tok", map[string]any{
		"volume_name": "vol-sec-1",
		"service_id":  "svc-sec",
		"project_id":  "p1",
	})
	if volRR.Code != http.StatusOK {
		t.Fatalf("volume provision failed: %d %s", volRR.Code, volRR.Body.String())
	}
	var volOut map[string]any
	_ = json.Unmarshal(volRR.Body.Bytes(), &volOut)
	if _, leaked := volOut["dek_hex"]; leaked {
		t.Fatal("raw dek_hex must never be returned by cryptd")
	}
	if !strings.HasPrefix(volOut["wrapped_dek"].(string), "grid:v1:") {
		t.Fatalf("expected grid:v1 wrapped_dek, got %v", volOut["wrapped_dek"])
	}

	// Manual lockdown -> /health 503, /v1/transit/encrypt 423, restored after /v1/vault/sync
	_ = doJSONReq(t, srv, http.MethodPost, "/v1/vault/lockdown", "secret-hdr-tok", map[string]any{"reason": "incident"})
	if rr := doJSONReq(t, srv, http.MethodGet, "/health", "", nil); rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 during lockdown, got %d", rr.Code)
	}
	if rr := doJSONReq(t, srv, http.MethodPost, "/v1/transit/encrypt", "secret-hdr-tok", map[string]any{
		"service_id":    "svc-sec",
		"plaintext_b64": base64.StdEncoding.EncodeToString([]byte("x")),
	}); rr.Code != http.StatusLocked {
		t.Fatalf("expected 423 during lockdown, got %d", rr.Code)
	}
	if rr := doJSONReq(t, srv, http.MethodPost, "/v1/vault/sync", "secret-hdr-tok", nil); rr.Code != http.StatusOK {
		t.Fatalf("vault sync recovery failed: %d", rr.Code)
	}

	// Service-scoping: transit & backup endpoints reject empty service_id (no control-plane fallback).
	if rr := doJSONReq(t, srv, http.MethodPost, "/v1/transit/encrypt", "secret-hdr-tok", map[string]any{
		"plaintext_b64": base64.StdEncoding.EncodeToString([]byte("x")),
	}); rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 when transit/encrypt omits service_id, got %d %s", rr.Code, rr.Body.String())
	}
	if rr := doJSONReq(t, srv, http.MethodPost, "/v1/transit/decrypt", "secret-hdr-tok", map[string]any{
		"token": encOut.Token,
	}); rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 when transit/decrypt omits service_id, got %d %s", rr.Code, rr.Body.String())
	}
	if rr := doJSONReq(t, srv, http.MethodPost, "/v1/backups/wrap-key", "secret-hdr-tok", map[string]any{
		"dek_b64": dekB64,
	}); rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 when backups/wrap-key omits service_id, got %d %s", rr.Code, rr.Body.String())
	}
	if rr := doJSONReq(t, srv, http.MethodPost, "/v1/backups/unwrap-key", "secret-hdr-tok", map[string]any{
		"wrapped_token": wrapOut.WrappedToken,
	}); rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 when backups/unwrap-key omits service_id, got %d %s", rr.Code, rr.Body.String())
	}
}
