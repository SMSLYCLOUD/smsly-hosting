package crypto

import (
	"bytes"
	"path/filepath"
	"testing"
)

func TestKeyHierarchyIsolationAndRoundtrip(t *testing.T) {
	mk := bytes.Repeat([]byte{0x42}, 32)
	smkW1, err := DeriveWeeklySMK(mk, 1, "cluster-a")
	if err != nil {
		t.Fatalf("DeriveWeeklySMK w1: %v", err)
	}
	smkW2, err := DeriveWeeklySMK(mk, 2, "cluster-a")
	if err != nil {
		t.Fatalf("DeriveWeeklySMK w2: %v", err)
	}
	if bytes.Equal(smkW1, smkW2) {
		t.Fatal("weekly SMKs must differ across weeks")
	}

	sekSvcA, err := DeriveServiceSEK(smkW1, "proj-1", "svc-alpha", 1)
	if err != nil {
		t.Fatalf("DeriveServiceSEK A: %v", err)
	}
	sekSvcB, err := DeriveServiceSEK(smkW1, "proj-1", "svc-beta", 1)
	if err != nil {
		t.Fatalf("DeriveServiceSEK B: %v", err)
	}
	sekPaas, err := DeriveServiceSEK(smkW1, "control-plane", ControlPlaneServiceID, 1)
	if err != nil {
		t.Fatalf("DeriveServiceSEK paas: %v", err)
	}
	if bytes.Equal(sekSvcA, sekSvcB) || bytes.Equal(sekSvcA, sekPaas) {
		t.Fatal("per-service and control-plane SEKs must all be cryptographically isolated")
	}

	tok, err := EncryptToken(sekSvcA, 1, "svc-alpha", "k1", "env_var", []byte("sk_live_secret"))
	if err != nil {
		t.Fatalf("EncryptToken: %v", err)
	}
	parsed, err := ParseToken(tok)
	if err != nil {
		t.Fatalf("ParseToken: %v", err)
	}
	plain, err := DecryptToken(sekSvcA, parsed, "env_var")
	if err != nil || string(plain) != "sk_live_secret" {
		t.Fatalf("DecryptToken failed: %v %q", err, plain)
	}

	// Wrong service key or wrong context must fail AEAD authentication.
	if _, err := DecryptToken(sekSvcB, parsed, "env_var"); err == nil {
		t.Fatal("expected decryption failure under svc-beta SEK")
	}
	if _, err := DecryptToken(sekSvcA, parsed, "other_ctx"); err == nil {
		t.Fatal("expected decryption failure under wrong AAD context")
	}
}

func TestRedisValueAEADAndKeyStore(t *testing.T) {
	smk := bytes.Repeat([]byte{0x99}, 32)
	sek, _ := DeriveServiceSEK(smk, "p1", "svc-1", 5)

	enc, err := EncryptRedisValue(sek, 5, "svc-1", []byte("session-token-xyz"))
	if err != nil {
		t.Fatalf("EncryptRedisValue: %v", err)
	}
	if !bytes.HasPrefix(enc, []byte(RedisValuePrefix)) {
		t.Fatalf("expected GRD1: prefix, got %q", enc)
	}
	w, blob, err := ParseRedisValueWeek(enc)
	if err != nil || w != 5 {
		t.Fatalf("ParseRedisValueWeek failed: week=%d err=%v", w, err)
	}
	dec, err := DecryptRedisValue(sek, w, "svc-1", blob)
	if err != nil || string(dec) != "session-token-xyz" {
		t.Fatalf("DecryptRedisValue failed: %v %q", err, dec)
	}
	if _, err := DecryptRedisValue(sek, w, "svc-other", blob); err == nil {
		t.Fatal("expected cross-service Redis value failure")
	}

	tmpDir := filepath.Join(t.TempDir(), "ks")
	ks, err := OpenKeyStore(tmpDir)
	if err != nil {
		t.Fatalf("OpenKeyStore: %v", err)
	}
	rawDEK, _ := GenerateRandomBytes(32)
	wrapped, err := WrapDEK(sek, 5, "svc-1", "k1", rawDEK)
	if err != nil {
		t.Fatalf("WrapDEK: %v", err)
	}
	rec := &ServiceKeyRecord{
		ServiceID:    "svc-1",
		ProjectID:    "p1",
		WrappedDEK:   wrapped,
		WrapWeek:     5,
		CurrentKeyID: "k1",
	}
	if err := ks.SaveService(rec); err != nil {
		t.Fatalf("SaveService: %v", err)
	}
	loaded, err := ks.GetService("svc-1")
	if err != nil || loaded == nil || loaded.WrappedDEK != wrapped {
		t.Fatalf("GetService mismatch: loaded=%+v err=%v", loaded, err)
	}
	unwrapped, _, err := UnwrapDEK(sek, loaded.WrappedDEK)
	if err != nil || !bytes.Equal(unwrapped, rawDEK) {
		t.Fatalf("UnwrapDEK mismatch: err=%v", err)
	}
}
