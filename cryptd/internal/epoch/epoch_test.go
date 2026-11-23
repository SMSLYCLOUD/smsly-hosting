package epoch

import (
	"bytes"
	"context"
	"errors"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/vault"
)

type toggleVault struct {
	fail atomic.Bool
	mk   []byte
}

func (v *toggleVault) Name() string { return "toggle-test-vault" }
func (v *toggleVault) Health(_ context.Context) error {
	if v.fail.Load() {
		return errors.New("vault offline")
	}
	return nil
}
func (v *toggleVault) FetchWeeklySMK(_ context.Context, week uint64, clusterSalt string) (*vault.SMKResult, error) {
	if v.fail.Load() {
		return nil, errors.New("vault unreachable")
	}
	smk, err := crypto.DeriveWeeklySMK(v.mk, week, clusterSalt)
	if err != nil {
		return nil, err
	}
	return &vault.SMKResult{
		Week:       week,
		SMK:        smk,
		Provider:   v.Name(),
		KeyVersion: "v1",
	}, nil
}

func TestWeeklyRolloverGraceLockdownAndOptInPolicy(t *testing.T) {
	now := time.Date(2026, 1, 5, 0, 0, 0, 0, time.UTC)
	v := &toggleVault{mk: bytes.Repeat([]byte{0x33}, 32)}
	store, err := crypto.OpenKeyStore(filepath.Join(t.TempDir(), "ks"))
	if err != nil {
		t.Fatalf("OpenKeyStore: %v", err)
	}

	cfg := DefaultConfig()
	cfg.EpochAnchor = now
	cfg.EpochDuration = 7 * 24 * time.Hour
	cfg.PreRotateLead = 24 * time.Hour
	cfg.GraceDuration = 24 * time.Hour
	cfg.FailClosedOnBoot = true

	mgr, err := NewManager(cfg, v, store)
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	mgr.SetNowFunc(func() time.Time { return now })

	var globalLocks atomic.Int32
	var svcLocks atomic.Int32
	mgr.SetCallbacks(
		func(string) { globalLocks.Add(1) },
		func(string, string) { svcLocks.Add(1) },
	)

	if err := mgr.SyncNow(context.Background()); err != nil {
		t.Fatalf("initial SyncNow: %v", err)
	}
	if st := mgr.Status(); st.State != StateActive || st.CurrentWeek != 1 {
		t.Fatalf("unexpected status: %+v", st)
	}

	// Register opted-in service AND opted-out service ("user chooses to use it or not").
	if err := mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-opted-in", ProjectID: "p1"}, true); err != nil {
		t.Fatalf("RegisterService opted-in: %v", err)
	}
	if err := mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-opted-out", ProjectID: "p1"}, false); err != nil {
		t.Fatalf("RegisterService opted-out: %v", err)
	}

	// Encrypt persistent data in Week 1 -> creates wrapped DEK with WrapWeek = 1
	tok, err := mgr.EncryptTransit("svc-opted-in", "p1", "db-col", "persistent", []byte("secret-db-val"))
	if err != nil {
		t.Fatalf("EncryptTransit persistent w1: %v", err)
	}

	// Roll over to Week 2 while vault is reachable -> DEK re-wraps automatically to Week 2!
	now = now.Add(7*24*time.Hour + time.Minute)
	mgr.Tick(context.Background())
	if st := mgr.Status(); st.CurrentWeek != 2 || st.State != StateActive {
		t.Fatalf("expected active week 2 after rollover, got %+v", st)
	}
	rec, _ := store.GetService("svc-opted-in")
	if rec.WrapWeek != 2 {
		t.Fatalf("expected O(1) DEK rewrap to WrapWeek=2, got %d", rec.WrapWeek)
	}
	// Historical persistent data still decrypts seamlessly under re-wrapped DEK!
	pt, err := mgr.DecryptTransit("svc-opted-in", "p1", "db-col", tok)
	if err != nil || string(pt) != "secret-db-val" {
		t.Fatalf("decrypt after week rollover failed: %v %q", err, pt)
	}

	// Per-service explicit Lock / Unlock
	epochTok, err := mgr.EncryptTransit("svc-opted-in", "p1", "db-col", "epoch", []byte("ephemeral-epoch-val"))
	if err != nil {
		t.Fatalf("EncryptTransit epoch w2: %v", err)
	}
	bakTok, err := mgr.WrapBackupDEK("svc-opted-in", "p1", bytes.Repeat([]byte{0x7A}, 32))
	if err != nil {
		t.Fatalf("WrapBackupDEK: %v", err)
	}
	if err := mgr.LockService("svc-opted-in", "manual lock"); err != nil {
		t.Fatalf("LockService: %v", err)
	}
	if svcLocks.Load() != 1 {
		t.Fatalf("expected onServiceLock callback to fire, got %d", svcLocks.Load())
	}
	if _, _, err := mgr.AuthorizeDBAccess("svc-opted-in"); err == nil {
		t.Fatal("expected locked opted-in service to be blocked at DB point of contact")
	}
	if _, err := mgr.DecryptTransit("svc-opted-in", "p1", "db-col", epochTok); !errors.Is(err, ErrServiceLocked) {
		t.Fatalf("expected ErrServiceLocked on locked service epoch DecryptTransit, got %v", err)
	}
	if _, err := mgr.UnwrapBackupDEK("svc-opted-in", "p1", bakTok); !errors.Is(err, ErrServiceLocked) {
		t.Fatalf("expected ErrServiceLocked on locked service UnwrapBackupDEK, got %v", err)
	}
	// Opted-out service remains unblocked ("choose to use it or not").
	if opted, _, err := mgr.AuthorizeDBAccess("svc-opted-out"); err != nil || opted {
		t.Fatalf("expected opted-out service to bypass cleanly, got opted=%v err=%v", opted, err)
	}
	if _, err := mgr.UnlockService("svc-opted-in", "p1", 10*24*time.Hour, false); err != nil {
		t.Fatalf("UnlockService: %v", err)
	}
	if ptEpoch, err := mgr.DecryptTransit("svc-opted-in", "p1", "db-col", epochTok); err != nil || string(ptEpoch) != "ephemeral-epoch-val" {
		t.Fatalf("expected epoch DecryptTransit to recover after unlock, got err=%v pt=%q", err, ptEpoch)
	}
	if rawBak, err := mgr.UnwrapBackupDEK("svc-opted-in", "p1", bakTok); err != nil || len(rawBak) != 32 {
		t.Fatalf("expected UnwrapBackupDEK to recover after unlock, got err=%v len=%d", err, len(rawBak))
	}

	// Roll into Week 3 while Vault is UNREACHABLE -> enters GRACE (24h window)!
	v.fail.Store(true)
	now = now.Add(7 * 24 * time.Hour)
	mgr.Tick(context.Background())
	if st := mgr.Status(); st.State != StateGrace {
		t.Fatalf("expected GRACE state when vault unreachable at rollover, got %s", st.State)
	}
	// Active service traffic keeps working during grace window!
	if _, _, err := mgr.AuthorizeDBAccess("svc-opted-in"); err != nil {
		t.Fatalf("expected access allowed during grace period, got %v", err)
	}

	// Advance past 24h grace deadline -> FAIL CLOSED into LOCKED state + fire OnGlobalLockdown!
	now = now.Add(24*time.Hour + time.Second)
	mgr.Tick(context.Background())
	if st := mgr.Status(); st.State != StateLocked {
		t.Fatalf("expected LOCKED state after grace period expiry, got %s", st.State)
	}
	if globalLocks.Load() == 0 {
		t.Fatal("expected OnGlobalLockdown callback when grace expired")
	}
	if _, _, err := mgr.AuthorizeDBAccess("svc-opted-in"); !errors.Is(err, ErrVaultLocked) {
		t.Fatalf("expected ErrVaultLocked in LOCKED state, got %v", err)
	}

	// Bring vault back online -> SyncNow restores ACTIVE for Week 3!
	v.fail.Store(false)
	if err := mgr.SyncNow(context.Background()); err != nil {
		t.Fatalf("SyncNow recovery failed: %v", err)
	}
	if st := mgr.Status(); st.State != StateActive || st.CurrentWeek != 3 {
		t.Fatalf("expected ACTIVE week 3 after recovery, got %+v", st)
	}

	// Restart recovery check: explicitly opted-out service must STAY opted out across restart!
	reloaded, err := NewManager(cfg, v, store)
	if err != nil {
		t.Fatalf("NewManager reload: %v", err)
	}
	if opted, allowed, err := reloaded.GetServicePolicy("svc-opted-out"); opted || !allowed || err != nil {
		t.Fatalf("opted-out preference lost across restart: opted=%v allowed=%v err=%v", opted, allowed, err)
	}
	if opted, _, _ := reloaded.GetServicePolicy("svc-opted-in"); !opted {
		t.Fatal("opted-in preference lost across restart")
	}

	// Regression checks:
	// 1. RegisterService called repeatedly without WrappedDEK must NOT overwrite existing DEK.
	beforeRec, _ := store.GetService("svc-opted-in")
	if err := mgr.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID: "svc-opted-in",
		ProjectID: "p1",
		DBRoles:   []string{"role_new"},
	}, true); err != nil {
		t.Fatalf("RegisterService repeat failed: %v", err)
	}
	afterRec, _ := store.GetService("svc-opted-in")
	if afterRec.WrappedDEK != beforeRec.WrappedDEK {
		t.Fatalf("RegisterService overwrote persistent DEK: before=%q after=%q", beforeRec.WrappedDEK, afterRec.WrappedDEK)
	}
	ptAfter, err := mgr.DecryptTransit("svc-opted-in", "p1", "db-col", tok)
	if err != nil || string(ptAfter) != "secret-db-val" {
		t.Fatalf("persistent ciphertext broken after repeat RegisterService: %v %q", err, ptAfter)
	}

	// 2. Opted-in service registered without UnlockService must NOT auto-grant a DB lease on first connect.
	if err := mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-never-unlocked", ProjectID: "p1"}, true); err != nil {
		t.Fatalf("RegisterService never-unlocked: %v", err)
	}
	if _, _, err := mgr.AuthorizeDBAccess("svc-never-unlocked"); !errors.Is(err, ErrServiceLocked) {
		t.Fatalf("expected ErrServiceLocked for never-unlocked service, got %v", err)
	}

	// 3. Persistent lease survives KeyStore reload; explicit LockService revokes it across reload.
	if err := mgr.RegisterService(&crypto.ServiceKeyRecord{ServiceID: "svc-persist", ProjectID: "p1"}, true); err != nil {
		t.Fatalf("RegisterService persist: %v", err)
	}
	if _, err := mgr.UnlockService("svc-persist", "p1", 0, true); err != nil {
		t.Fatalf("UnlockService persist: %v", err)
	}
	reloadedPersist, err := NewManager(cfg, v, store)
	if err != nil {
		t.Fatalf("NewManager reload persist: %v", err)
	}
	if err := reloadedPersist.SyncNow(context.Background()); err != nil {
		t.Fatalf("reloadedPersist SyncNow: %v", err)
	}
	if _, _, err := reloadedPersist.AuthorizeDBAccess("svc-persist"); err != nil {
		t.Fatalf("expected persistent lease to survive controller restart, got %v", err)
	}

	// 4. ForceLockdown stays LOCKED across weekly rollover Tick().
	mgr.ForceLockdown("manual lock test")
	now = now.Add(8 * 24 * time.Hour)
	mgr.Tick(context.Background())
	if st := mgr.Status(); st.State != StateLocked {
		t.Fatalf("expected ForceLockdown to survive week rollover Tick, got %s", st.State)
	}
}
