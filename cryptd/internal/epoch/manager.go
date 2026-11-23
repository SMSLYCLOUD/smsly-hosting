package epoch

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"log"
	"sync"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/vault"
)

// State represents the global cryptographic controller lifecycle state.
type State string

const (
	StateInitializing State = "INITIALIZING"
	StateActive       State = "ACTIVE"
	StateGrace        State = "GRACE"
	StateLocked       State = "LOCKED"
)

var (
	ErrVaultLocked       = errors.New("cryptd is in LOCKED state; database and key access severed")
	ErrServiceLocked     = errors.New("service access is locked by encryption controller")
	ErrServiceNotOptedIn = errors.New("service encryption is opted out")
	ErrRollbackDetected  = errors.New("vault returned week older than monotonic floor")
)

// Config tunes epoch timing, grace period, and lock behavior.
type Config struct {
	EpochDuration    time.Duration
	PreRotateLead    time.Duration
	GraceDuration    time.Duration
	PollInterval     time.Duration
	EpochAnchor      time.Time
	ClusterSalt      string
	FailClosedOnBoot bool
	DefaultLeaseTTL  time.Duration
	DefaultOptedIn   bool
}

// DefaultConfig returns weekly rollover (168h) with a 24h grace period and fail-closed boot.
func DefaultConfig() Config {
	return Config{
		EpochDuration:    7 * 24 * time.Hour,
		PreRotateLead:    24 * time.Hour,
		GraceDuration:    24 * time.Hour,
		PollInterval:     1 * time.Minute,
		EpochAnchor:      time.Date(2025, 1, 6, 0, 0, 0, 0, time.UTC),
		ClusterSalt:      "trulay-grid-default",
		FailClosedOnBoot: true,
		DefaultLeaseTTL:  2 * time.Hour,
		DefaultOptedIn:   false,
	}
}

// ServiceLease stores runtime unlock state and opt-in status for a service.
type ServiceLease struct {
	ServiceID   string    `json:"service_id"`
	ProjectID   string    `json:"project_id"`
	OptedIn     bool      `json:"opted_in"`
	Locked      bool      `json:"locked"`
	LockReason  string    `json:"lock_reason,omitempty"`
	UnlockedAt  time.Time `json:"unlocked_at,omitempty"`
	ExpiresAt   time.Time `json:"expires_at,omitempty"`
	Persistent  bool      `json:"persistent_lease"`
}

// StatusSnapshot is a safe, secret-free telemetry view for /v1/status.
type StatusSnapshot struct {
	State          State     `json:"state"`
	CurrentWeek    uint64    `json:"current_week"`
	PreviousWeek   uint64    `json:"previous_week,omitempty"`
	NextWeekStaged bool      `json:"next_week_staged"`
	MinValidWeek   uint64    `json:"min_valid_week"`
	Provider       string    `json:"provider"`
	KeyVersion     string    `json:"key_version"`
	GraceDeadline  time.Time `json:"grace_deadline,omitempty"`
	LastSyncAt     time.Time `json:"last_sync_at,omitempty"`
	LastError      string    `json:"last_error,omitempty"`
	OptedServices  int       `json:"opted_services"`
	ActiveLeases   int       `json:"active_leases"`
}

// Manager owns the RAM-only SMK hierarchy, weekly pre-rotation, grace period, and service leases.
type Manager struct {
	mu       sync.RWMutex
	cfg      Config
	provider vault.Provider
	store    *crypto.KeyStore
	nowFn    func() time.Time

	state         State
	currentWeek   uint64
	currentSMK    []byte
	prevWeek      uint64
	prevSMK       []byte
	nextWeek      uint64
	nextSMK       []byte
	minValidWeek  uint64
	keyVersion    string
	lastSyncAt    time.Time
	lastError     string
	graceDeadline time.Time

	leases  map[string]*ServiceLease
	lockSeq uint64
	loopOnce sync.Once

	onGlobalLockdown func(reason string)
	onServiceLock    func(serviceID, reason string)
	onServiceUnlock  func(serviceID string) error
}

// NewManager initializes the Epoch & Key Hierarchy Manager.
func NewManager(cfg Config, provider vault.Provider, store *crypto.KeyStore) (*Manager, error) {
	if provider == nil {
		return nil, errors.New("vault provider cannot be nil")
	}
	if cfg.EpochDuration <= 0 {
		cfg.EpochDuration = 7 * 24 * time.Hour
	}
	if cfg.GraceDuration < 0 {
		cfg.GraceDuration = 0
	}
	if cfg.PreRotateLead <= 0 || cfg.PreRotateLead >= cfg.EpochDuration {
		cfg.PreRotateLead = cfg.EpochDuration / 7
	}
	if cfg.PollInterval <= 0 {
		cfg.PollInterval = time.Minute
	}
	if cfg.DefaultLeaseTTL <= 0 {
		cfg.DefaultLeaseTTL = 2 * time.Hour
	}
	if cfg.EpochAnchor.IsZero() {
		cfg.EpochAnchor = time.Date(2025, 1, 6, 0, 0, 0, 0, time.UTC)
	}

	m := &Manager{
		cfg:      cfg,
		provider: provider,
		store:    store,
		nowFn:    func() time.Time { return time.Now().UTC() },
		state:    StateInitializing,
		leases:   make(map[string]*ServiceLease),
	}

	if store != nil {
		if st, err := store.LoadEpochState(); err == nil && st != nil {
			m.minValidWeek = st.MinValidWeek
		}
		if recs, err := store.ListServices(); err == nil {
			for _, rec := range recs {
				m.leases[rec.ServiceID] = &ServiceLease{
					ServiceID:  rec.ServiceID,
					ProjectID:  rec.ProjectID,
					OptedIn:    rec.OptedIn,
					Locked:     rec.Locked,
					ExpiresAt:  rec.LeaseExpiresAt,
					Persistent: rec.LeasePersistent || rec.ServiceID == crypto.ControlPlaneServiceID,
				}
			}
		}
	}
	// Always keep control plane entry tracked; whether opted-in or active depends on caller/env.
	if _, ok := m.leases[crypto.ControlPlaneServiceID]; !ok {
		m.leases[crypto.ControlPlaneServiceID] = &ServiceLease{
			ServiceID:  crypto.ControlPlaneServiceID,
			ProjectID:  "control-plane",
			OptedIn:    cfg.DefaultOptedIn,
			Persistent: true,
		}
	}
	return m, nil
}

// SetNowFunc overrides time source (intended for deterministic unit tests).
func (m *Manager) SetNowFunc(fn func() time.Time) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if fn != nil {
		m.nowFn = fn
	}
}

// SetCallbacks installs hooks fired on global lockdown or per-service lock.
func (m *Manager) SetCallbacks(onGlobal func(reason string), onService func(serviceID, reason string)) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.onGlobalLockdown = onGlobal
	m.onServiceLock = onService
}

// SetServiceUnlockCallback installs a hook fired after unlocking a service lease (used to unlock encrypted volumes).
func (m *Manager) SetServiceUnlockCallback(fn func(serviceID string) error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.onServiceUnlock = fn
}

// WeekForTime returns the 1-based epoch week index for timestamp t.
func (m *Manager) WeekForTime(t time.Time) uint64 {
	if t.Before(m.cfg.EpochAnchor) {
		return 1
	}
	return uint64(t.Sub(m.cfg.EpochAnchor)/m.cfg.EpochDuration) + 1
}

func (m *Manager) weekEndTime(week uint64) time.Time {
	return m.cfg.EpochAnchor.Add(time.Duration(week) * m.cfg.EpochDuration)
}

// Start performs the initial vault sync and starts the background rotation & lease watchdog.
func (m *Manager) Start(ctx context.Context) error {
	m.loopOnce.Do(func() {
		go m.loop(ctx)
	})
	if err := m.SyncNow(ctx); err != nil {
		if m.cfg.FailClosedOnBoot {
			m.ForceLockdown("boot vault sync failed: " + err.Error())
		}
		return err
	}
	return nil
}

func (m *Manager) loop(ctx context.Context) {
	ticker := time.NewTicker(m.cfg.PollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			m.ZeroAllMemory()
			return
		case <-ticker.C:
			m.Tick(ctx)
		}
	}
}

// Tick advances epoch timers, triggers pre-rotation or rollover, enforces grace deadline,
// and revokes expired service leases.
func (m *Manager) Tick(ctx context.Context) {
	m.mu.Lock()
	now := m.nowFn()
	targetWeek := m.WeekForTime(now)
	state := m.state
	currWeek := m.currentWeek
	graceDeadline := m.graceDeadline
	hasNext := m.nextWeek == currWeek+1 && len(m.nextSMK) == crypto.KeySize
	needPreRotate := state == StateActive && !hasNext && now.Add(m.cfg.PreRotateLead).After(m.weekEndTime(currWeek))

	var expiredServices []string
	for svcID, lease := range m.leases {
		if lease.OptedIn && !lease.Locked && !lease.Persistent && !lease.ExpiresAt.IsZero() && now.After(lease.ExpiresAt) {
			lease.Locked = true
			lease.LockReason = "lease TTL expired"
			expiredServices = append(expiredServices, svcID)
		}
	}
	svcCb := m.onServiceLock
	m.mu.Unlock()

	if svcCb != nil {
		for _, svcID := range expiredServices {
			svcCb(svcID, "lease TTL expired")
		}
	}

	// Remain fail-closed once locked until explicit /v1/vault/sync recovery.
	if state == StateLocked {
		return
	}

	// If next week's SMK was already pre-staged and rollover happened, switch immediately!
	if targetWeek > currWeek {
		if m.tryActivateStagedNext(targetWeek) {
			m.rewrapServices(ctx)
		} else {
			if err := m.SyncNow(ctx); err != nil {
				m.mu.Lock()
				if m.state == StateActive {
					m.state = StateGrace
					m.graceDeadline = now.Add(m.cfg.GraceDuration)
					m.lastError = err.Error()
					log.Printf("WARN [cryptd]: week rollover %d->%d vault unreachable (%v); entering GRACE until %s",
						currWeek, targetWeek, err, m.graceDeadline.Format(time.RFC3339))
				}
				if m.state == StateGrace && !m.graceDeadline.IsZero() && !now.Before(m.graceDeadline) {
					m.mu.Unlock()
					m.ForceLockdown("grace period expired without vault sync")
					return
				}
				m.mu.Unlock()
			}
		}
	} else if state == StateGrace && !graceDeadline.IsZero() && !now.Before(graceDeadline) {
		m.ForceLockdown("grace period expired without vault sync")
		return
	} else if needPreRotate {
		_ = m.preRotateNext(ctx, currWeek+1)
	}
}

func (m *Manager) tryActivateStagedNext(targetWeek uint64) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.nextWeek != targetWeek || len(m.nextSMK) != crypto.KeySize {
		return false
	}
	if len(m.prevSMK) > 0 {
		crypto.ZeroBytes(m.prevSMK)
	}
	m.prevWeek = m.currentWeek
	m.prevSMK = m.currentSMK
	m.currentWeek = m.nextWeek
	m.currentSMK = m.nextSMK
	m.nextWeek = 0
	m.nextSMK = nil
	if m.currentWeek > m.minValidWeek {
		m.minValidWeek = m.currentWeek
	}
	m.state = StateActive
	m.graceDeadline = time.Time{}
	m.lastError = ""
	m.persistEpochLocked()
	return true
}

func (m *Manager) preRotateNext(ctx context.Context, nextW uint64) error {
	sctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	res, err := m.provider.FetchWeeklySMK(sctx, nextW, m.cfg.ClusterSalt)
	if err != nil {
		return err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if len(m.nextSMK) > 0 {
		crypto.ZeroBytes(m.nextSMK)
	}
	m.nextWeek = nextW
	m.nextSMK = res.SMK
	return nil
}

// SyncNow fetches the current week's SMK from the external vault and transitions ACTIVE.
func (m *Manager) SyncNow(ctx context.Context) error {
	now := m.nowFn()
	targetWeek := m.WeekForTime(now)

	m.mu.RLock()
	floor := m.minValidWeek
	seq := m.lockSeq
	m.mu.RUnlock()
	if floor > 0 && targetWeek < floor {
		return ErrRollbackDetected
	}

	sctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	res, err := m.provider.FetchWeeklySMK(sctx, targetWeek, m.cfg.ClusterSalt)
	if err != nil {
		m.mu.Lock()
		m.lastError = err.Error()
		m.mu.Unlock()
		return err
	}
	if len(res.SMK) != crypto.KeySize {
		return fmt.Errorf("vault returned unexpected SMK length %d", len(res.SMK))
	}

	m.mu.Lock()
	if m.lockSeq != seq {
		crypto.ZeroBytes(res.SMK)
		m.mu.Unlock()
		return ErrVaultLocked
	}
	prevW := m.currentWeek
	if prevW != 0 && prevW != targetWeek {
		if len(m.prevSMK) > 0 {
			crypto.ZeroBytes(m.prevSMK)
		}
		m.prevWeek = prevW
		m.prevSMK = m.currentSMK
	} else if len(m.currentSMK) > 0 {
		crypto.ZeroBytes(m.currentSMK)
	}
	m.currentWeek = targetWeek
	m.currentSMK = res.SMK
	m.keyVersion = res.KeyVersion
	m.lastSyncAt = now
	m.lastError = ""
	m.graceDeadline = time.Time{}
	m.state = StateActive
	if targetWeek > m.minValidWeek {
		m.minValidWeek = targetWeek
	}
	if len(m.nextSMK) > 0 && m.nextWeek <= targetWeek {
		crypto.ZeroBytes(m.nextSMK)
		m.nextSMK = nil
		m.nextWeek = 0
	}
	m.persistEpochLocked()
	m.mu.Unlock()

	if prevW != 0 && prevW != targetWeek {
		m.rewrapServices(ctx)
	} else if len(m.prevSMK) == 0 {
		// Recovery from lockdown or cold restart may have un-rewrapped records from last week.
		m.rewrapServices(ctx)
	}
	return nil
}

func (m *Manager) persistEpochLocked() {
	if m.store == nil {
		return
	}
	_ = m.store.SaveEpochState(&crypto.EpochStateFile{
		MinValidWeek:    m.minValidWeek,
		CurrentWeek:     m.currentWeek,
		EpochAnchorUnix: m.cfg.EpochAnchor.Unix(),
		LastSyncAt:      m.lastSyncAt,
	})
}

func (m *Manager) rewrapServices(ctx context.Context) {
	if m.store == nil {
		return
	}
	recs, err := m.store.ListServices()
	if err != nil {
		return
	}
	for _, rec := range recs {
		if rec.WrappedDEK == "" && len(rec.VolumeDEKs) == 0 {
			continue
		}
		if err := m.rewrapServiceRecord(ctx, rec); err != nil {
			log.Printf("WARN [cryptd]: rewrap service %s failed: %v", rec.ServiceID, err)
		}
	}
}

func (m *Manager) rewrapTokenForService(
	ctx context.Context,
	rec *crypto.ServiceKeyRecord,
	wrapped string,
	keyID string,
	currW uint64,
	currSMK []byte,
	prevW uint64,
	prevSMK []byte,
) (string, bool, error) {
	parsed, err := crypto.ParseToken(wrapped)
	if err != nil {
		return "", false, err
	}
	if parsed.Week == currW {
		return wrapped, false, nil
	}
	var oldSMK []byte
	var zeroOld bool
	switch parsed.Week {
	case currW:
		if len(currSMK) == crypto.KeySize {
			oldSMK = currSMK
		}
	case prevW:
		if len(prevSMK) == crypto.KeySize {
			oldSMK = prevSMK
		}
	}
	if len(oldSMK) != crypto.KeySize {
		// Transiently fetch the historical week's SMK (e.g. cold boot or lockdown recovery
		// across a week rollover) to O(1) re-wrap to currentWeek, then immediately zero it.
		sctx, cancel := context.WithTimeout(ctx, 15*time.Second)
		res, verr := m.provider.FetchWeeklySMK(sctx, parsed.Week, m.cfg.ClusterSalt)
		cancel()
		if verr == nil && len(res.SMK) == crypto.KeySize {
			oldSMK = res.SMK
			zeroOld = true
		} else {
			return "", false, fmt.Errorf("wrapped DEK week %d unavailable for rewrap to week %d", parsed.Week, currW)
		}
	}
	if zeroOld {
		defer crypto.ZeroBytes(oldSMK)
	}
	if len(oldSMK) != crypto.KeySize {
		return "", false, errors.New("missing source SMK for rewrap")
	}
	oldSEK, err := crypto.DeriveServiceSEK(oldSMK, rec.ProjectID, rec.ServiceID, parsed.Week)
	if err != nil {
		return "", false, err
	}
	defer crypto.ZeroBytes(oldSEK)

	dek, _, err := crypto.UnwrapDEK(oldSEK, wrapped)
	if err != nil {
		return "", false, err
	}
	defer crypto.ZeroBytes(dek)

	newSEK, err := crypto.DeriveServiceSEK(currSMK, rec.ProjectID, rec.ServiceID, currW)
	if err != nil {
		return "", false, err
	}
	defer crypto.ZeroBytes(newSEK)

	if keyID == "" {
		keyID = parsed.KeyID
	}
	newWrapped, err := crypto.WrapDEK(newSEK, currW, rec.ServiceID, keyID, dek)
	if err != nil {
		return "", false, err
	}
	return newWrapped, true, nil
}

func (m *Manager) rewrapServiceRecord(ctx context.Context, rec *crypto.ServiceKeyRecord) error {
	m.mu.RLock()
	currW := m.currentWeek
	currSMK := append([]byte(nil), m.currentSMK...)
	prevW := m.prevWeek
	prevSMK := append([]byte(nil), m.prevSMK...)
	m.mu.RUnlock()
	defer crypto.ZeroBytes(currSMK)
	defer crypto.ZeroBytes(prevSMK)

	changed := false
	now := m.nowFn()
	if rec.WrappedDEK != "" && rec.WrapWeek != currW {
		newWrapped, updated, err := m.rewrapTokenForService(ctx, rec, rec.WrappedDEK, rec.CurrentKeyID, currW, currSMK, prevW, prevSMK)
		if err != nil {
			return err
		}
		if updated {
			rec.WrappedDEK = newWrapped
			rec.WrapWeek = currW
			rec.RotatedAt = now
			changed = true
		}
	}
	for volName, vrec := range rec.VolumeDEKs {
		if vrec.WrappedDEK == "" || vrec.WrapWeek == currW {
			continue
		}
		newWrapped, updated, err := m.rewrapTokenForService(ctx, rec, vrec.WrappedDEK, vrec.KeyID, currW, currSMK, prevW, prevSMK)
		if err != nil {
			log.Printf("WARN [cryptd]: rewrap volume %s for %s failed: %v", volName, rec.ServiceID, err)
			continue
		}
		if updated {
			vrec.WrappedDEK = newWrapped
			vrec.WrapWeek = currW
			vrec.RotatedAt = now
			rec.VolumeDEKs[volName] = vrec
			changed = true
		}
	}
	if !changed {
		return nil
	}
	return m.store.SaveService(rec)
}

// ForceLockdown immediately zeroes all key material in memory, transitions to LOCKED,
// and severs all DB/Redis/volume access.
func (m *Manager) ForceLockdown(reason string) {
	m.mu.Lock()
	m.lockSeq++
	m.state = StateLocked
	if reason != "" {
		m.lastError = reason
	}
	crypto.ZeroBytes(m.currentSMK)
	crypto.ZeroBytes(m.prevSMK)
	crypto.ZeroBytes(m.nextSMK)
	m.currentSMK = nil
	m.prevSMK = nil
	m.nextSMK = nil
	m.prevWeek = 0
	m.nextWeek = 0
	cb := m.onGlobalLockdown
	m.mu.Unlock()

	if cb != nil {
		cb(reason)
	}
}

// ZeroAllMemory wipes cached secrets on shutdown.
func (m *Manager) ZeroAllMemory() {
	m.mu.Lock()
	defer m.mu.Unlock()
	crypto.ZeroBytes(m.currentSMK)
	crypto.ZeroBytes(m.prevSMK)
	crypto.ZeroBytes(m.nextSMK)
	m.currentSMK = nil
	m.prevSMK = nil
	m.nextSMK = nil
	m.prevWeek = 0
	m.nextWeek = 0
}

// Status returns a secret-free status snapshot.
func (m *Manager) Status() StatusSnapshot {
	m.mu.RLock()
	defer m.mu.RUnlock()
	opted := 0
	active := 0
	now := m.nowFn()
	for _, l := range m.leases {
		if l.OptedIn {
			opted++
			if !l.Locked && (l.Persistent || (!l.ExpiresAt.IsZero() && now.Before(l.ExpiresAt))) {
				active++
			}
		}
	}
	return StatusSnapshot{
		State:          m.state,
		CurrentWeek:    m.currentWeek,
		PreviousWeek:   m.prevWeek,
		NextWeekStaged: len(m.nextSMK) == crypto.KeySize,
		MinValidWeek:   m.minValidWeek,
		Provider:       m.provider.Name(),
		KeyVersion:     m.keyVersion,
		GraceDeadline:  m.graceDeadline,
		LastSyncAt:     m.lastSyncAt,
		LastError:      m.lastError,
		OptedServices:  opted,
		ActiveLeases:   active,
	}
}

// RegisterService configures opt-in status and persistent record for a service.
// Because encryption is opt-in ("choose to use it or not"), services with OptedIn=false
// bypass enforcement and operate in passthrough mode.
func (m *Manager) RegisterService(rec *crypto.ServiceKeyRecord, optedIn bool) error {
	if rec == nil {
		return errors.New("nil service record")
	}
	cleanID, err := crypto.ValidateIdentifier(rec.ServiceID)
	if err != nil {
		return err
	}
	rec.ServiceID = cleanID
	if rec.ProjectID == "" {
		if cleanID == crypto.ControlPlaneServiceID {
			rec.ProjectID = "control-plane"
		} else {
			rec.ProjectID = "default"
		}
	}

	m.mu.Lock()
	existing := m.leases[cleanID]
	if existing == nil {
		existing = &ServiceLease{
			ServiceID:  cleanID,
			ProjectID:  rec.ProjectID,
			OptedIn:    optedIn,
			Persistent: cleanID == crypto.ControlPlaneServiceID,
		}
		m.leases[cleanID] = existing
	} else {
		existing.ProjectID = rec.ProjectID
		existing.OptedIn = optedIn
	}
	locked := existing.Locked
	m.mu.Unlock()

	rec.OptedIn = optedIn
	rec.Locked = locked

	var existingRec *crypto.ServiceKeyRecord
	if m.store != nil {
		if loaded, err := m.store.GetService(cleanID); err == nil && loaded != nil {
			existingRec = loaded
		}
	}
	if existingRec != nil {
		if rec.WrappedDEK == "" && existingRec.WrappedDEK != "" {
			rec.WrappedDEK = existingRec.WrappedDEK
			rec.WrapWeek = existingRec.WrapWeek
			rec.CurrentKeyID = existingRec.CurrentKeyID
			rec.RotatedAt = existingRec.RotatedAt
		}
		if len(existingRec.VolumeNames) > 0 || len(rec.VolumeNames) > 0 {
			seen := make(map[string]struct{}, len(existingRec.VolumeNames)+len(rec.VolumeNames))
			merged := make([]string, 0, len(existingRec.VolumeNames)+len(rec.VolumeNames))
			for _, vn := range existingRec.VolumeNames {
				if vn != "" {
					if _, ok := seen[vn]; !ok {
						seen[vn] = struct{}{}
						merged = append(merged, vn)
					}
				}
			}
			for _, vn := range rec.VolumeNames {
				if vn != "" {
					if _, ok := seen[vn]; !ok {
						seen[vn] = struct{}{}
						merged = append(merged, vn)
					}
				}
			}
			rec.VolumeNames = merged
		}
		if len(existingRec.VolumeDEKs) > 0 {
			if rec.VolumeDEKs == nil {
				rec.VolumeDEKs = make(map[string]crypto.VolumeDEKRecord, len(existingRec.VolumeDEKs))
			}
			for k, v := range existingRec.VolumeDEKs {
				if _, exists := rec.VolumeDEKs[k]; !exists {
					rec.VolumeDEKs[k] = v
				}
			}
		}
		if rec.Cipher == "" {
			rec.Cipher = existingRec.Cipher
		}
		if rec.OwnerUID == 0 {
			rec.OwnerUID = existingRec.OwnerUID
		}
		if rec.OwnerGID == 0 {
			rec.OwnerGID = existingRec.OwnerGID
		}
		if !rec.LeasePersistent && existingRec.LeasePersistent {
			rec.LeasePersistent = true
		}
		if rec.LeaseExpiresAt.IsZero() {
			rec.LeaseExpiresAt = existingRec.LeaseExpiresAt
		}
		if !existingRec.CreatedAt.IsZero() {
			rec.CreatedAt = existingRec.CreatedAt
		}
	}
	if rec.CreatedAt.IsZero() {
		rec.CreatedAt = m.nowFn()
	}
	if optedIn && rec.WrappedDEK == "" && m.store != nil {
		// Provision initial wrapped DEK ONLY if none existed in keystore.
		if sek, week, err := m.ActiveSEK(rec.ProjectID, rec.ServiceID); err == nil {
			defer crypto.ZeroBytes(sek)
			rawDEK, gerr := crypto.GenerateRandomBytes(crypto.KeySize)
			if gerr == nil {
				defer crypto.ZeroBytes(rawDEK)
				if wrapped, werr := crypto.WrapDEK(sek, week, rec.ServiceID, crypto.DefaultKeyID, rawDEK); werr == nil {
					rec.WrappedDEK = wrapped
					rec.WrapWeek = week
					rec.CurrentKeyID = crypto.DefaultKeyID
					rec.RotatedAt = m.nowFn()
				}
			}
		}
	}
	if m.store != nil {
		if err := m.store.SaveService(rec); err != nil {
			return err
		}
	}
	return nil
}

// GetServicePolicy reports whether serviceID is opted-in and currently allowed (unlocked).
func (m *Manager) GetServicePolicy(serviceID string) (optedIn bool, allowed bool, err error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	lease, ok := m.leases[serviceID]
	if !ok {
		return m.cfg.DefaultOptedIn, !m.cfg.DefaultOptedIn, nil
	}
	if !lease.OptedIn {
		return false, true, nil
	}
	if m.state == StateLocked {
		return true, false, ErrVaultLocked
	}
	if lease.Locked {
		return true, false, ErrServiceLocked
	}
	if !lease.Persistent && !lease.ExpiresAt.IsZero() && m.nowFn().After(lease.ExpiresAt) {
		return true, false, ErrServiceLocked
	}
	return true, true, nil
}

// UnlockService activates or renews a service lease (making DB/Redis/transit available).
func (m *Manager) UnlockService(serviceID, projectID string, ttl time.Duration, persistent bool) (*ServiceLease, error) {
	cleanID, err := crypto.ValidateIdentifier(serviceID)
	if err != nil {
		return nil, err
	}
	if ttl <= 0 {
		ttl = m.cfg.DefaultLeaseTTL
	}

	m.mu.Lock()
	if m.state == StateLocked || m.state == StateInitializing {
		m.mu.Unlock()
		return nil, ErrVaultLocked
	}
	lease, ok := m.leases[cleanID]
	if !ok {
		if projectID == "" {
			projectID = "default"
		}
		lease = &ServiceLease{
			ServiceID: cleanID,
			ProjectID: projectID,
			OptedIn:   true,
		}
		m.leases[cleanID] = lease
	} else if projectID != "" {
		lease.ProjectID = projectID
	}
	lease.OptedIn = true
	lease.Locked = false
	lease.LockReason = ""
	now := m.nowFn()
	lease.UnlockedAt = now
	lease.Persistent = persistent || cleanID == crypto.ControlPlaneServiceID
	if lease.Persistent {
		lease.ExpiresAt = time.Time{}
	} else {
		lease.ExpiresAt = now.Add(ttl)
	}
	if m.store != nil {
		if rec, _ := m.store.GetService(cleanID); rec != nil {
			if rec.Locked || rec.LeasePersistent != lease.Persistent || !rec.LeaseExpiresAt.Equal(lease.ExpiresAt) {
				rec.Locked = false
				rec.LeasePersistent = lease.Persistent
				rec.LeaseExpiresAt = lease.ExpiresAt
				_ = m.store.SaveService(rec)
			}
		}
	}
	cp := *lease
	unlockCB := m.onServiceUnlock
	m.mu.Unlock()

	if unlockCB != nil {
		if uerr := unlockCB(cleanID); uerr != nil {
			_ = m.LockService(cleanID, "volume unlock failed: "+uerr.Error())
			return nil, uerr
		}
	}
	return &cp, nil
}

// LockService immediately revokes a service lease and closes all its active DB/Redis connections.
func (m *Manager) LockService(serviceID, reason string) error {
	cleanID, err := crypto.ValidateIdentifier(serviceID)
	if err != nil {
		return err
	}
	if reason == "" {
		reason = "explicitly locked"
	}

	m.mu.Lock()
	lease, ok := m.leases[cleanID]
	if !ok {
		lease = &ServiceLease{
			ServiceID: cleanID,
			ProjectID: "default",
			OptedIn:   true,
		}
		m.leases[cleanID] = lease
	}
	lease.Locked = true
	lease.LockReason = reason
	lease.Persistent = false
	lease.ExpiresAt = time.Time{}
	cb := m.onServiceLock
	m.mu.Unlock()

	if m.store != nil {
		if rec, _ := m.store.GetService(cleanID); rec != nil {
			rec.Locked = true
			rec.LeasePersistent = false
			rec.LeaseExpiresAt = time.Time{}
			_ = m.store.SaveService(rec)
		}
	}
	if cb != nil {
		cb(cleanID, reason)
	}
	return nil
}

// AuthorizeDBAccess enforces point-of-contact lease checks before opening/maintaining a DB/Redis session.
func (m *Manager) AuthorizeDBAccess(serviceID string) (optedIn bool, projectID string, err error) {
	m.mu.Lock()
	lease, ok := m.leases[serviceID]
	if !ok {
		if !m.cfg.DefaultOptedIn {
			m.mu.Unlock()
			return false, "default", nil
		}
		lease = &ServiceLease{
			ServiceID:  serviceID,
			ProjectID:  "default",
			OptedIn:    true,
			UnlockedAt: m.nowFn(),
			ExpiresAt:  m.nowFn().Add(m.cfg.DefaultLeaseTTL),
		}
		m.leases[serviceID] = lease
	}
	if !lease.OptedIn {
		proj := lease.ProjectID
		m.mu.Unlock()
		return false, proj, nil
	}
	if m.state == StateLocked || m.state == StateInitializing {
		m.mu.Unlock()
		return true, lease.ProjectID, ErrVaultLocked
	}
	if lease.Locked {
		m.mu.Unlock()
		return true, lease.ProjectID, ErrServiceLocked
	}
	now := m.nowFn()
	if !lease.Persistent {
		if lease.ExpiresAt.IsZero() {
			m.mu.Unlock()
			return true, lease.ProjectID, ErrServiceLocked
		}
		if now.After(lease.ExpiresAt) {
			lease.Locked = true
			lease.LockReason = "lease expired"
			m.mu.Unlock()
			return true, lease.ProjectID, ErrServiceLocked
		}
	}
	proj := lease.ProjectID
	m.mu.Unlock()
	return true, proj, nil
}

// ActiveSEK derives the current week's SEK_{project,service,week} into RAM (caller must ZeroBytes).
func (m *Manager) ActiveSEK(projectID, serviceID string) ([]byte, uint64, error) {
	m.mu.RLock()
	state := m.state
	week := m.currentWeek
	smk := append([]byte(nil), m.currentSMK...)
	lease := m.leases[serviceID]
	if lease != nil {
		if lease.OptedIn && lease.Locked {
			m.mu.RUnlock()
			crypto.ZeroBytes(smk)
			return nil, 0, ErrServiceLocked
		}
		if projectID == "" {
			projectID = lease.ProjectID
		}
	}
	m.mu.RUnlock()
	defer crypto.ZeroBytes(smk)

	if state != StateActive && state != StateGrace {
		return nil, 0, ErrVaultLocked
	}
	if len(smk) != crypto.KeySize {
		return nil, 0, ErrVaultLocked
	}
	sek, err := crypto.DeriveServiceSEK(smk, projectID, serviceID, week)
	return sek, week, err
}

// SEKForWeek derives a service SEK for currWeek or prevWeek (or grace/decryption within floor).
func (m *Manager) SEKForWeek(projectID, serviceID string, week uint64) ([]byte, error) {
	m.mu.RLock()
	state := m.state
	currW := m.currentWeek
	prevW := m.prevWeek
	var smk []byte
	switch week {
	case currW:
		smk = append([]byte(nil), m.currentSMK...)
	case prevW:
		smk = append([]byte(nil), m.prevSMK...)
	}
	if projectID == "" {
		if l := m.leases[serviceID]; l != nil {
			projectID = l.ProjectID
		}
	}
	m.mu.RUnlock()
	defer crypto.ZeroBytes(smk)

	if state != StateActive && state != StateGrace {
		return nil, ErrVaultLocked
	}
	if len(smk) != crypto.KeySize {
		return nil, fmt.Errorf("SMK for week %d is not active", week)
	}
	return crypto.DeriveServiceSEK(smk, projectID, serviceID, week)
}

// SEKForWeekOrFetch resolves SEK_{project,service,week} from RAM when active,
// or falls back to fetching the historical weekly SMK from the vault when reachable.
func (m *Manager) SEKForWeekOrFetch(projectID, serviceID string, week uint64) ([]byte, error) {
	sek, err := m.SEKForWeek(projectID, serviceID, week)
	if err == nil {
		return sek, nil
	}
	st := m.Status()
	if st.State != StateActive && st.State != StateGrace {
		return nil, err
	}
	m.mu.RLock()
	resolvedProj := projectID
	if resolvedProj == "" {
		if l := m.leases[serviceID]; l != nil {
			resolvedProj = l.ProjectID
		}
	}
	m.mu.RUnlock()

	sctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	res, verr := m.provider.FetchWeeklySMK(sctx, week, m.cfg.ClusterSalt)
	if verr != nil || len(res.SMK) != crypto.KeySize {
		return nil, err
	}
	defer crypto.ZeroBytes(res.SMK)
	return crypto.DeriveServiceSEK(res.SMK, resolvedProj, serviceID, week)
}

// checkServiceNotLocked returns ErrVaultLocked if the controller is globally locked
// or ErrServiceLocked if the service was explicitly locked (in RAM/store) or its lease expired.
func (m *Manager) checkServiceNotLocked(cleanID string) error {
	m.mu.RLock()
	state := m.state
	lease := m.leases[cleanID]
	leaseLocked := false
	if lease != nil && lease.OptedIn {
		if lease.Locked {
			leaseLocked = true
		} else if !lease.Persistent && !lease.ExpiresAt.IsZero() && m.nowFn().After(lease.ExpiresAt) {
			leaseLocked = true
		}
	}
	m.mu.RUnlock()

	if state == StateLocked || state == StateInitializing {
		return ErrVaultLocked
	}
	if leaseLocked {
		return ErrServiceLocked
	}
	if m.store != nil {
		if rec, err := m.store.GetService(cleanID); err == nil && rec != nil && rec.Locked {
			return ErrServiceLocked
		}
	}
	return nil
}

// EnsureServiceDEK loads or provisions the service's persistent 256-bit DEK wrapped under SEK_{s,w}.
// Returns (dek, keyID, projectID); caller MUST ZeroBytes(dek).
func (m *Manager) EnsureServiceDEK(serviceID, projectID string) ([]byte, string, string, error) {
	cleanID, err := crypto.ValidateIdentifier(serviceID)
	if err != nil {
		return nil, "", "", err
	}
	if err := m.checkServiceNotLocked(cleanID); err != nil {
		return nil, "", "", err
	}
	if m.store == nil {
		return nil, "", "", errors.New("keystore unavailable")
	}
	rec, err := m.store.GetService(cleanID)
	if err != nil {
		return nil, "", "", err
	}
	if rec == nil {
		if projectID == "" {
			if cleanID == crypto.ControlPlaneServiceID {
				projectID = "control-plane"
			} else {
				projectID = "default"
			}
		}
		rec = &crypto.ServiceKeyRecord{
			ServiceID:    cleanID,
			ProjectID:    projectID,
			CurrentKeyID: crypto.DefaultKeyID,
			CreatedAt:    m.nowFn(),
		}
	} else if rec.ProjectID != "" {
		projectID = rec.ProjectID
	}

	if rec.WrappedDEK != "" {
		parsed, err := crypto.ParseToken(rec.WrappedDEK)
		if err != nil {
			return nil, "", "", err
		}
		sek, err := m.SEKForWeekOrFetch(projectID, cleanID, parsed.Week)
		if err != nil {
			return nil, "", "", err
		}
		defer crypto.ZeroBytes(sek)
		dek, _, err := crypto.UnwrapDEK(sek, rec.WrappedDEK)
		if err != nil {
			return nil, "", "", err
		}
		return dek, parsed.KeyID, projectID, nil
	}

	// Provision new DEK wrapped with current SEK_w
	sek, week, err := m.ActiveSEK(projectID, cleanID)
	if err != nil {
		return nil, "", "", err
	}
	defer crypto.ZeroBytes(sek)
	dek, err := crypto.GenerateRandomBytes(crypto.KeySize)
	if err != nil {
		return nil, "", "", err
	}
	wrapped, err := crypto.WrapDEK(sek, week, cleanID, crypto.DefaultKeyID, dek)
	if err != nil {
		crypto.ZeroBytes(dek)
		return nil, "", "", err
	}
	rec.WrappedDEK = wrapped
	rec.WrapWeek = week
	rec.CurrentKeyID = crypto.DefaultKeyID
	rec.RotatedAt = m.nowFn()
	if err := m.store.SaveService(rec); err != nil {
		crypto.ZeroBytes(dek)
		return nil, "", "", err
	}
	return dek, crypto.DefaultKeyID, projectID, nil
}

// EncryptTransit encrypts plaintext for serviceID using either ephemeral weekly SEK ("epoch")
// or the service's persistent wrapped DEK ("persistent").
func (m *Manager) EncryptTransit(serviceID, projectID, contextStr, mode string, plaintext []byte) (string, error) {
	if m.Status().State == StateLocked {
		return "", ErrVaultLocked
	}
	if mode == "" || mode == "epoch" {
		sek, week, err := m.ActiveSEK(projectID, serviceID)
		if err != nil {
			return "", err
		}
		defer crypto.ZeroBytes(sek)
		return crypto.EncryptToken(sek, week, serviceID, crypto.DefaultKeyID, contextStr, plaintext)
	}
	if mode == "persistent" {
		dek, keyID, _, err := m.EnsureServiceDEK(serviceID, projectID)
		if err != nil {
			return "", err
		}
		defer crypto.ZeroBytes(dek)
		subkey, err := crypto.DerivePurposeSubkey(dek, "persistent-transit")
		if err != nil {
			return "", err
		}
		defer crypto.ZeroBytes(subkey)
		aad := crypto.FormatAAD(0, serviceID, keyID, contextStr)
		blob, err := crypto.EncryptAEAD(subkey, plaintext, aad)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("grid:v1:0:%s:%s:%s", serviceID, keyID, base64.RawURLEncoding.EncodeToString(blob)), nil
	}
	return "", fmt.Errorf("unknown transit mode %q", mode)
}

// DecryptTransit decrypts a grid:v1 token for serviceID.
func (m *Manager) DecryptTransit(serviceID, projectID, contextStr, token string) ([]byte, error) {
	cleanID, err := crypto.ValidateIdentifier(serviceID)
	if err != nil {
		return nil, err
	}
	if err := m.checkServiceNotLocked(cleanID); err != nil {
		return nil, err
	}
	serviceID = cleanID
	parsed, err := crypto.ParseToken(token)
	if err != nil {
		return nil, err
	}
	if parsed.ServiceID != serviceID {
		return nil, fmt.Errorf("cross-service token mismatch: expected %s got %s", serviceID, parsed.ServiceID)
	}
	if parsed.Week == 0 {
		dek, _, _, err := m.EnsureServiceDEK(serviceID, projectID)
		if err != nil {
			return nil, err
		}
		defer crypto.ZeroBytes(dek)
		subkey, err := crypto.DerivePurposeSubkey(dek, "persistent-transit")
		if err != nil {
			return nil, err
		}
		defer crypto.ZeroBytes(subkey)
		aad := crypto.FormatAAD(0, parsed.ServiceID, parsed.KeyID, contextStr)
		return crypto.DecryptAEAD(subkey, parsed.Ciphertext, aad)
	}
	sek, err := m.SEKForWeekOrFetch(projectID, serviceID, parsed.Week)
	if err != nil {
		return nil, err
	}
	defer crypto.ZeroBytes(sek)
	return crypto.DecryptToken(sek, parsed, contextStr)
}

// WrapBackupDEK wraps a backup tarball/stream DEK under the service SEK_w.
func (m *Manager) WrapBackupDEK(serviceID, projectID string, rawDEK []byte) (string, error) {
	cleanID, err := crypto.ValidateIdentifier(serviceID)
	if err != nil {
		return "", err
	}
	if err := m.checkServiceNotLocked(cleanID); err != nil {
		return "", err
	}
	serviceID = cleanID
	sek, week, err := m.ActiveSEK(projectID, serviceID)
	if err != nil {
		return "", err
	}
	defer crypto.ZeroBytes(sek)
	subkey, err := crypto.DerivePurposeSubkey(sek, "backup-wrap")
	if err != nil {
		return "", err
	}
	defer crypto.ZeroBytes(subkey)
	aad := crypto.FormatAAD(week, serviceID, "bak1", "backup-dek")
	blob, err := crypto.EncryptAEAD(subkey, rawDEK, aad)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("grid:v1:%d:%s:bak1:%s", week, serviceID, base64.RawURLEncoding.EncodeToString(blob)), nil
}

// UnwrapBackupDEK recovers a backup DEK, returning an error if wrong service or locked.
func (m *Manager) UnwrapBackupDEK(serviceID, projectID, wrappedToken string) ([]byte, error) {
	cleanID, err := crypto.ValidateIdentifier(serviceID)
	if err != nil {
		return nil, err
	}
	if err := m.checkServiceNotLocked(cleanID); err != nil {
		return nil, err
	}
	parsed, err := crypto.ParseToken(wrappedToken)
	if err != nil {
		return nil, err
	}
	if parsed.ServiceID != cleanID {
		return nil, fmt.Errorf("backup token belongs to service %s, not %s", parsed.ServiceID, cleanID)
	}
	sek, err := m.SEKForWeekOrFetch(projectID, parsed.ServiceID, parsed.Week)
	if err != nil {
		return nil, err
	}
	defer crypto.ZeroBytes(sek)
	subkey, err := crypto.DerivePurposeSubkey(sek, "backup-wrap")
	if err != nil {
		return nil, err
	}
	defer crypto.ZeroBytes(subkey)
	aad := crypto.FormatAAD(parsed.Week, parsed.ServiceID, parsed.KeyID, "backup-dek")
	return crypto.DecryptAEAD(subkey, parsed.Ciphertext, aad)
}
