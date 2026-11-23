package volumes

import (
	"context"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/epoch"
	"smsly-hosting/cryptd/internal/vault"
)

func TestVolumeProvisionUnlockLockCleanup(t *testing.T) {
	tmp := t.TempDir()
	store, err := crypto.OpenKeyStore(filepath.Join(tmp, "ks"))
	if err != nil {
		t.Fatalf("OpenKeyStore: %v", err)
	}
	prov, err := vault.NewProvider(vault.Config{
		ProviderName:  "dev_memory",
		AllowDevVault: true,
		DevMasterKey:  strings.Repeat("ef", 32),
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
	volMgr, err := NewManager(Config{
		BaseDir:   filepath.Join(tmp, "vols"),
		Backend:   "mock",
		AllowMock: true,
	}, mgr, store, nil)
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}

	if rep := volMgr.Health(); !rep.OK || !rep.Fscrypt || !rep.Cryptsetup {
		t.Fatalf("unexpected volume health: %+v", rep)
	}

	res, err := volMgr.Provision(context.Background(), ProvisionRequest{
		VolumeName: "pg-tenant-vol",
		ServiceID:  "svc-vol-1",
		ProjectID:  "proj-vol-1",
	})
	if err != nil || !res.OK || !strings.HasPrefix(res.WrappedDEK, "grid:v1:") {
		t.Fatalf("Provision failed: %+v err=%v", res, err)
	}

	if err := volMgr.Lock(context.Background(), "pg-tenant-vol", "svc-vol-1"); err != nil {
		t.Fatalf("Lock failed: %v", err)
	}
	if _, _, err := mgr.AuthorizeDBAccess("svc-vol-1"); err == nil {
		t.Fatal("expected locked volume service to be locked in epoch manager")
	}

	unlockRes, err := volMgr.Unlock(context.Background(), UnlockRequest{
		VolumeName: "pg-tenant-vol",
		ServiceID:  "svc-vol-1",
		ProjectID:  "proj-vol-1",
		WrappedDEK: res.WrappedDEK,
	})
	if err != nil || !unlockRes.OK {
		t.Fatalf("Unlock failed: %+v err=%v", unlockRes, err)
	}

	if lockedCount := volMgr.LockAll(context.Background()); lockedCount != 1 {
		t.Fatalf("expected LockAll to lock 1 registered volume, got %d", lockedCount)
	}

	if err := volMgr.Cleanup(context.Background(), "pg-tenant-vol", "svc-vol-1"); err != nil {
		t.Fatalf("Cleanup failed: %v", err)
	}
}

type scriptMockRunner struct {
	calls [][]string
}

func (r *scriptMockRunner) LookPath(file string) (string, error) {
	return "/usr/bin/" + file, nil
}

func (r *scriptMockRunner) Run(_ context.Context, stdin []byte, name string, args ...string) ([]byte, error) {
	rec := append([]string{name}, args...)
	r.calls = append(r.calls, rec)
	sub := ""
	vol := ""
	if len(args) > 0 {
		sub = args[0]
	}
	if len(args) > 1 {
		vol = args[1]
	}
	switch sub {
	case "provision":
		if len(stdin) == 0 {
			return nil, fmt.Errorf("missing stdin DEK")
		}
		return []byte(fmt.Sprintf(`{"ok":true,"backend":"fscrypt","host_path":"/var/lib/smsly/encrypted-volumes/fscrypt/%s/data","cipher":"fscrypt-v2-aes-256-xts"}`+"\n", vol)), nil
	case "unlock":
		if len(stdin) == 0 {
			return nil, fmt.Errorf("missing stdin DEK")
		}
		return []byte(fmt.Sprintf(`{"ok":true,"backend":"fscrypt","host_path":"/var/lib/smsly/encrypted-volumes/fscrypt/%s/data","cipher":"fscrypt-v2"}`+"\n", vol)), nil
	case "lock":
		return []byte(fmt.Sprintf(`{"ok":true,"locked":"%s"}`+"\n", vol)), nil
	case "cleanup":
		return []byte(fmt.Sprintf(`{"ok":true,"cleaned":"%s"}`+"\n", vol)), nil
	default:
		return nil, fmt.Errorf("unknown subcommand %q", sub)
	}
}

func TestVolumeScriptProtocolIdempotencyAndMultiVolume(t *testing.T) {
	tmp := t.TempDir()
	store, err := crypto.OpenKeyStore(filepath.Join(tmp, "ks"))
	if err != nil {
		t.Fatalf("OpenKeyStore: %v", err)
	}
	prov, err := vault.NewProvider(vault.Config{
		ProviderName:  "dev_memory",
		AllowDevVault: true,
		DevMasterKey:  strings.Repeat("12", 32),
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
	runner := &scriptMockRunner{}
	volMgr, err := NewManager(Config{
		BaseDir:    filepath.Join(tmp, "vols"),
		ScriptPath: "/opt/smsly-hosting/scripts/smsly-volume-crypt.sh",
		Backend:    "auto",
	}, mgr, store, runner)
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}

	res1, err := volMgr.Provision(context.Background(), ProvisionRequest{
		VolumeName: "vol-a",
		ServiceID:  "svc-multi",
		ProjectID:  "proj-1",
		OwnerUID:   1000,
		OwnerGID:   1000,
	})
	if err != nil {
		t.Fatalf("Provision vol-a: %v", err)
	}
	if res1.HostPath != "/var/lib/smsly/encrypted-volumes/fscrypt/vol-a/data" {
		t.Fatalf("expected parsed script host_path, got %q", res1.HostPath)
	}
	if !slices.Contains(runner.calls[0], "--uid") || !slices.Contains(runner.calls[0], "--gid") {
		t.Fatalf("expected --uid and --gid flags passed to script: %v", runner.calls[0])
	}

	// Re-deploy Provision of same volume must unlock & reuse existing DEK (NOT overwrite!).
	resRe, err := volMgr.Provision(context.Background(), ProvisionRequest{
		VolumeName: "vol-a",
		ServiceID:  "svc-multi",
		ProjectID:  "proj-1",
		OwnerUID:   1000,
		OwnerGID:   1000,
	})
	if err != nil {
		t.Fatalf("Re-provision vol-a: %v", err)
	}
	if resRe.WrappedDEK != res1.WrappedDEK {
		t.Fatalf("re-provision rotated DEK instead of reusing existing wrapped key")
	}
	if runner.calls[1][1] != "unlock" {
		t.Fatalf("expected unlock on re-provision, got %v", runner.calls[1])
	}

	// Provision second volume under same service -> both remain tracked, cleanup one leaves other intact.
	res2, err := volMgr.Provision(context.Background(), ProvisionRequest{
		VolumeName: "vol-b",
		ServiceID:  "svc-multi",
		ProjectID:  "proj-1",
	})
	if err != nil {
		t.Fatalf("Provision vol-b: %v", err)
	}
	if err := volMgr.Lock(context.Background(), "vol-b", "svc-multi"); err != nil {
		t.Fatalf("Lock vol-b: %v", err)
	}
	if err := volMgr.Cleanup(context.Background(), "vol-a", "svc-multi"); err != nil {
		t.Fatalf("Cleanup vol-a: %v", err)
	}
	rec, err := store.GetService("svc-multi")
	if err != nil || rec == nil {
		t.Fatalf("expected svc-multi record preserved after cleaning one volume, err=%v", err)
	}
	if _, ok := rec.VolumeDEKs["vol-b"]; !ok || rec.VolumeDEKs["vol-b"].WrappedDEK != res2.WrappedDEK {
		t.Fatalf("expected vol-b DEK preserved after vol-a cleanup: %+v", rec.VolumeDEKs)
	}

	// Regression guard: wiring SetServiceUnlockCallback -> UnlockServiceVolumes must NOT recurse/deadlock
	// across Provision, Unlock, or LockService -> UnlockService!
	mgr.SetServiceUnlockCallback(func(svcID string) error {
		return volMgr.UnlockServiceVolumes(context.Background(), svcID)
	})
	if err := mgr.LockService("svc-multi", "stop"); err != nil {
		t.Fatalf("LockService: %v", err)
	}
	beforeUnlockCalls := len(runner.calls)
	if _, err := mgr.UnlockService("svc-multi", "proj-1", 0, false); err != nil {
		t.Fatalf("UnlockService with callback wired failed: %v", err)
	}
	if len(runner.calls) <= beforeUnlockCalls || runner.calls[beforeUnlockCalls][1] != "unlock" {
		t.Fatalf("expected UnlockService callback to invoke volume unlock on vol-b, calls=%v", runner.calls[beforeUnlockCalls:])
	}
	if _, err := volMgr.Provision(context.Background(), ProvisionRequest{
		VolumeName: "vol-c",
		ServiceID:  "svc-multi",
		ProjectID:  "proj-1",
	}); err != nil {
		t.Fatalf("Provision with UnlockService callback wired must not deadlock: %v", err)
	}
}
