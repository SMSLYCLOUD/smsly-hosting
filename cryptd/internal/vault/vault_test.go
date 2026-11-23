package vault

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"smsly-hosting/cryptd/internal/crypto"
)

func TestHTTPRootVaultDerivesWithoutExposingMasterKey(t *testing.T) {
	mk := bytes.Repeat([]byte{0x7A}, 32)
	handler, err := NewRootVaultHandler(RootVaultConfig{
		MasterKey:    mk,
		KeyVersion:   "mk-prod-2026",
		HMACSecret:   "hmac-super-secret",
		AuthToken:    "tok-123",
		AllowedNodes: map[string]bool{"node-1": true},
	})
	if err != nil {
		t.Fatalf("NewRootVaultHandler: %v", err)
	}
	ts := httptest.NewTLSServer(handler)
	defer ts.Close()

	clientCfg := Config{
		ProviderName:       "http_vault",
		Endpoint:           ts.URL,
		NodeID:             "node-1",
		HMACSecret:         "hmac-super-secret",
		AuthToken:          "tok-123",
		InsecureSkipVerify: true,
	}
	prov, err := NewProvider(clientCfg)
	if err != nil {
		t.Fatalf("NewProvider http_vault: %v", err)
	}

	res, err := prov.FetchWeeklySMK(context.Background(), 12, "prod-cluster")
	if err != nil {
		t.Fatalf("FetchWeeklySMK: %v", err)
	}
	expectedSMK, _ := crypto.DeriveWeeklySMK(mk, 12, "prod-cluster")
	if !bytes.Equal(res.SMK, expectedSMK) {
		t.Fatal("derived SMK from root vault does not match reference HKDF")
	}
	if bytes.Equal(res.SMK, mk) {
		t.Fatal("root vault leaked raw master key instead of derived SMK")
	}
	if res.KeyVersion != "mk-prod-2026" {
		t.Fatalf("unexpected key version %q", res.KeyVersion)
	}

	// Unauthorized node must be forbidden.
	badCfg := clientCfg
	badCfg.NodeID = "rogue-node"
	badProv, _ := NewProvider(badCfg)
	if _, err := badProv.FetchWeeklySMK(context.Background(), 12, "prod-cluster"); err == nil {
		t.Fatal("expected rogue node to be rejected by root vault")
	}
}

func TestHashiCorpTransitAndKV2AndInfisicalAndKMSProviders(t *testing.T) {
	mk := bytes.Repeat([]byte{0x55}, 32)

	// Mock HashiCorp Vault server supporting AppRole, Transit HMAC, and KV v2
	hcSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/auth/approle/login":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"auth": map[string]any{"client_token": "hvs.approle-token"},
			})
		case "/v1/transit/hmac/grid-mk/sha2-256":
			if r.Header.Get("X-Vault-Token") != "hvs.approle-token" {
				http.Error(w, "unauthorized", http.StatusForbidden)
				return
			}
			var body struct {
				Input string `json:"input"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			rawIn, _ := base64.StdEncoding.DecodeString(body.Input)
			mac := hmac.New(sha256.New, mk)
			mac.Write(rawIn)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"data": map[string]any{
					"hmac": "vault:v3:" + base64.StdEncoding.EncodeToString(mac.Sum(nil)),
				},
			})
		case "/v1/secret/data/trulay/mk":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"data": map[string]any{
					"data":     map[string]any{"master_key": base64.StdEncoding.EncodeToString(mk)},
					"metadata": map[string]any{"version": 7},
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer hcSrv.Close()

	// 1. HashiCorp Transit HMAC + AppRole
	hcTransit, err := NewProvider(Config{
		ProviderName:    "hashicorp",
		Endpoint:        hcSrv.URL,
		VaultRoleID:     "role-1",
		VaultSecretID:   "secret-1",
		VaultTransitKey: "grid-mk",
	})
	if err != nil {
		t.Fatalf("NewProvider hashicorp transit: %v", err)
	}
	resTransit, err := hcTransit.FetchWeeklySMK(context.Background(), 4, "c1")
	if err != nil || len(resTransit.SMK) != 32 || resTransit.KeyVersion != "v3" {
		t.Fatalf("hashicorp transit failed: %+v err=%v", resTransit, err)
	}

	// 2. HashiCorp KV v2
	hcKV, err := NewProvider(Config{
		ProviderName: "openbao",
		Endpoint:     hcSrv.URL,
		AuthToken:    "hvs.static",
		VaultKVPath:  "trulay/mk",
	})
	if err != nil {
		t.Fatalf("NewProvider kv2: %v", err)
	}
	resKV, err := hcKV.FetchWeeklySMK(context.Background(), 4, "c1")
	if err != nil || len(resKV.SMK) != 32 || resKV.KeyVersion != "kv-v7" {
		t.Fatalf("hashicorp kv2 failed: %+v err=%v", resKV, err)
	}

	// 3. Infisical Universal Auth
	infSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/auth/universal-auth/login":
			_ = json.NewEncoder(w).Encode(map[string]any{"accessToken": "inf.jwt"})
		case "/api/v3/secrets/raw/GRID_MASTER_KEY":
			if r.Header.Get("Authorization") != "Bearer inf.jwt" {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"secret": map[string]any{
					"secretValue": "b64:" + base64.StdEncoding.EncodeToString(mk),
					"version":     2,
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer infSrv.Close()

	infProv, err := NewProvider(Config{
		ProviderName:          "infisical",
		Endpoint:              infSrv.URL,
		InfisicalClientID:     "cid",
		InfisicalClientSecret: "csec",
		InfisicalWorkspaceID:  "ws-1",
	})
	if err != nil {
		t.Fatalf("NewProvider infisical: %v", err)
	}
	resInf, err := infProv.FetchWeeklySMK(context.Background(), 4, "c1")
	if err != nil || !bytes.Equal(resInf.SMK, resKV.SMK) {
		t.Fatalf("infisical failed or SMK mismatch: err=%v", err)
	}

	// 4. Cloud KMS envelope decrypt
	kmsSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"plaintext": base64.StdEncoding.EncodeToString(mk),
			"KeyId":     "arn:aws:kms:us-east-1:123:key/abc",
		})
	}))
	defer kmsSrv.Close()

	kmsProv, err := NewProvider(Config{
		ProviderName:     "cloud_kms",
		Endpoint:         kmsSrv.URL,
		KMSMode:          "decrypt",
		KMSKeyID:         "abc",
		KMSCiphertextB64: base64.StdEncoding.EncodeToString([]byte("wrapped-blob")),
	})
	if err != nil {
		t.Fatalf("NewProvider cloud_kms: %v", err)
	}
	resKMS, err := kmsProv.FetchWeeklySMK(context.Background(), 4, "c1")
	if err != nil || !bytes.Equal(resKMS.SMK, resKV.SMK) {
		t.Fatalf("cloud_kms failed: err=%v", err)
	}
}

func TestDevMemoryFailClosedAndCommandProvider(t *testing.T) {
	if _, err := NewProvider(Config{
		ProviderName:  "dev_memory",
		DevMasterKey:  base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x11}, 32)),
		AllowDevVault: false,
	}); err == nil {
		t.Fatal("dev_memory must fail closed unless AllowDevVault=true")
	}

	if runtime.GOOS == "windows" {
		return
	}
	scriptPath := filepath.Join(t.TempDir(), "vault-hook.sh")
	b64SMK := base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xAB}, 32))
	err := os.WriteFile(scriptPath, []byte("#!/bin/sh\necho '{\"smk_b64\":\""+b64SMK+"\",\"key_version\":\"hsm-1\"}'\n"), 0o700)
	if err != nil {
		t.Fatalf("write script: %v", err)
	}
	prov, err := NewProvider(Config{
		ProviderName: "command",
		CommandBin:   scriptPath,
	})
	if err != nil {
		t.Fatalf("NewProvider command: %v", err)
	}
	res, err := prov.FetchWeeklySMK(context.Background(), 9, "salt")
	if err != nil || len(res.SMK) != 32 || res.KeyVersion != "hsm-1" {
		t.Fatalf("command provider failed: %+v err=%v", res, err)
	}
}
