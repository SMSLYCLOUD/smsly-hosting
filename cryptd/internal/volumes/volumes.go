package volumes

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/epoch"
)

var validVolumeNameRe = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,126}$`)

// ProvisionRequest describes a volume provisioning request from Django/napd.
type ProvisionRequest struct {
	VolumeName string `json:"volume_name"`
	ServiceID  string `json:"service_id,omitempty"`
	ProjectID  string `json:"project_id,omitempty"`
	DEKHex     string `json:"dek_hex,omitempty"`     // Optional; if omitted, cryptd generates DEK internally
	WrappedDEK string `json:"wrapped_dek,omitempty"` // Optional existing wrapped token
	KeyID      string `json:"key_id,omitempty"`
	OwnerUID   int    `json:"owner_uid,omitempty"`
	OwnerGID   int    `json:"owner_gid,omitempty"`
	Cipher     string `json:"cipher,omitempty"`
}

// UnlockRequest unlocks a previously provisioned volume.
type UnlockRequest struct {
	VolumeName string `json:"volume_name"`
	ServiceID  string `json:"service_id,omitempty"`
	ProjectID  string `json:"project_id,omitempty"`
	DEKHex     string `json:"dek_hex,omitempty"`
	WrappedDEK string `json:"wrapped_dek,omitempty"`
	KeyID      string `json:"key_id,omitempty"`
	OwnerUID   int    `json:"owner_uid,omitempty"`
	OwnerGID   int    `json:"owner_gid,omitempty"`
}

// HealthReport summarizes host filesystem encryption capabilities.
type HealthReport struct {
	OK          bool        `json:"ok"`
	Backend     string      `json:"backend"`
	Fscrypt     bool        `json:"fscrypt"`
	Cryptsetup  bool        `json:"cryptsetup"`
	EpochState  epoch.State `json:"state"`
	CurrentWeek uint64      `json:"week"`
}

// ProvisionResult describes the provisioned volume path and wrapped key metadata.
// Raw DEKs are never returned to callers.
type ProvisionResult struct {
	OK         bool   `json:"ok"`
	VolumeName string `json:"volume_name"`
	ServiceID  string `json:"service_id"`
	HostPath   string `json:"host_path"`
	Cipher     string `json:"cipher"`
	WrappedDEK string `json:"wrapped_dek"`
	KeyID      string `json:"key_id"`
	Week       uint64 `json:"week"`
}

// CommandRunner abstracts host subprocess calls (`fscrypt`, `cryptsetup`, etc.) for clean testing.
type CommandRunner interface {
	LookPath(file string) (string, error)
	Run(ctx context.Context, stdin []byte, name string, args ...string) ([]byte, error)
}

type osCommandRunner struct{}

func (osCommandRunner) LookPath(file string) (string, error) {
	return exec.LookPath(file)
}

func (osCommandRunner) Run(ctx context.Context, stdin []byte, name string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	if len(stdin) > 0 {
		cmd.Stdin = bytes.NewReader(stdin)
	}
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return out.Bytes(), err
}

// Config configures encrypted host volume management.
type Config struct {
	BaseDir      string // Default: /var/lib/smsly/encrypted-volumes
	ScriptPath   string // Optional helper script (scripts/smsly-volume-crypt.sh)
	Backend      string // "auto", "fscrypt", "luks", or "mock" (explicit tests only)
	AllowMock    bool
	CmdTimeout   time.Duration
}

// Manager manages per-service encrypted directories / LUKS volumes using epoch-wrapped DEKs.
type Manager struct {
	mu     sync.Mutex
	cfg    Config
	epochs *epoch.Manager
	store  *crypto.KeyStore
	runner CommandRunner
}

// NewManager initializes the Volume Manager.
func NewManager(cfg Config, epochs *epoch.Manager, store *crypto.KeyStore, runner CommandRunner) (*Manager, error) {
	if strings.TrimSpace(cfg.BaseDir) == "" {
		cfg.BaseDir = "/var/lib/smsly/encrypted-volumes"
	}
	if strings.TrimSpace(cfg.Backend) == "" {
		cfg.Backend = "auto"
	}
	if cfg.CmdTimeout <= 0 {
		cfg.CmdTimeout = 120 * time.Second
	}
	if runner == nil {
		runner = osCommandRunner{}
	}
	return &Manager{
		cfg:    cfg,
		epochs: epochs,
		store:  store,
		runner: runner,
	}, nil
}

// Health inspects available Linux encryption primitives and epoch lock status.
func (m *Manager) Health() HealthReport {
	_, fscryptErr := m.runner.LookPath("fscrypt")
	_, luksErr := m.runner.LookPath("cryptsetup")
	st := m.epochs.Status()
	hasFS := fscryptErr == nil
	hasLuks := luksErr == nil
	if m.cfg.Backend == "mock" && m.cfg.AllowMock {
		hasFS = true
		hasLuks = true
	}
	return HealthReport{
		OK:          (hasFS || hasLuks) && st.State != epoch.StateLocked,
		Backend:     m.cfg.Backend,
		Fscrypt:     hasFS,
		Cryptsetup:  hasLuks,
		EpochState:  st.State,
		CurrentWeek: st.CurrentWeek,
	}
}

func sanitizeVolumeName(name string) (string, error) {
	trimmed := strings.TrimSpace(name)
	if !validVolumeNameRe.MatchString(trimmed) || strings.Contains(trimmed, "..") {
		return "", fmt.Errorf("invalid volume_name %q", name)
	}
	return trimmed, nil
}

func resolveVolumeServiceID(volumeName, serviceID string) string {
	if strings.TrimSpace(serviceID) != "" {
		return strings.TrimSpace(serviceID)
	}
	return "vol-" + volumeName
}

// Provision creates (or unlocks if already registered) an encrypted host volume for a service without returning raw DEKs.
func (m *Manager) Provision(ctx context.Context, req ProvisionRequest) (*ProvisionResult, error) {
	volName, err := sanitizeVolumeName(req.VolumeName)
	if err != nil {
		return nil, err
	}
	if m.epochs.Status().State == epoch.StateLocked {
		return nil, epoch.ErrVaultLocked
	}
	svcID, err := crypto.ValidateIdentifier(resolveVolumeServiceID(volName, req.ServiceID))
	if err != nil {
		return nil, err
	}
	projID := req.ProjectID
	if projID == "" {
		projID = "default"
	}

	// Idempotent re-deploy: if this volume already has a wrapped DEK in keystore,
	// unlock and reuse the existing key instead of rotating over existing ciphertext!
	if strings.TrimSpace(req.DEKHex) == "" && m.store != nil {
		if existingRec, gerr := m.store.GetService(svcID); gerr == nil && existingRec != nil {
			existingWrapped := ""
			if vrec, ok := existingRec.VolumeDEKs[volName]; ok && vrec.WrappedDEK != "" {
				existingWrapped = vrec.WrappedDEK
			} else if existingRec.WrappedDEK != "" {
				for _, vn := range existingRec.VolumeNames {
					if vn == volName {
						existingWrapped = existingRec.WrappedDEK
						break
					}
				}
			}
			if existingWrapped != "" {
				return m.Unlock(ctx, UnlockRequest{
					VolumeName: volName,
					ServiceID:  svcID,
					ProjectID:  projID,
					WrappedDEK: existingWrapped,
					OwnerUID:   req.OwnerUID,
					OwnerGID:   req.OwnerGID,
				})
			}
		}
	}

	var dek []byte
	if strings.TrimSpace(req.DEKHex) != "" {
		dek, err = hex.DecodeString(strings.TrimSpace(req.DEKHex))
		if err != nil || len(dek) != crypto.KeySize {
			return nil, errors.New("dek_hex must decode to 32 bytes")
		}
	} else {
		// Daemon generates DEK in locked memory so raw key never touches Python.
		dek, err = crypto.GenerateRandomBytes(crypto.KeySize)
		if err != nil {
			return nil, err
		}
	}
	defer crypto.ZeroBytes(dek)

	sek, week, err := m.epochs.ActiveSEK(projID, svcID)
	if err != nil {
		return nil, err
	}
	defer crypto.ZeroBytes(sek)

	keyID := req.KeyID
	if keyID == "" {
		keyID = crypto.DefaultKeyID
	}
	wrapped, err := crypto.WrapDEK(sek, week, svcID, keyID, dek)
	if err != nil {
		return nil, err
	}

	hostPath, cipher, err := m.provisionHostVolume(ctx, volName, dek, req.OwnerUID, req.OwnerGID)
	if err != nil {
		return nil, err
	}

	// Register/update keystore record with per-volume DEK mapping and acquire lease.
	vrec := crypto.VolumeDEKRecord{
		VolumeName: volName,
		WrappedDEK: wrapped,
		WrapWeek:   week,
		KeyID:      keyID,
		Cipher:     cipher,
		HostPath:   hostPath,
		OwnerUID:   req.OwnerUID,
		OwnerGID:   req.OwnerGID,
		RotatedAt:  time.Now().UTC(),
	}
	if err := m.epochs.RegisterService(&crypto.ServiceKeyRecord{
		ServiceID:    svcID,
		ProjectID:    projID,
		VolumeNames:  []string{volName},
		VolumeDEKs:   map[string]crypto.VolumeDEKRecord{volName: vrec},
		Cipher:       cipher,
		OwnerUID:     req.OwnerUID,
		OwnerGID:     req.OwnerGID,
		CurrentKeyID: keyID,
		WrapWeek:     week,
	}, true); err != nil {
		_ = m.cleanupHostVolume(ctx, volName)
		return nil, err
	}
	_, _ = m.epochs.UnlockService(svcID, projID, 0, false)

	return &ProvisionResult{
		OK:         true,
		VolumeName: volName,
		ServiceID:  svcID,
		HostPath:   hostPath,
		Cipher:     cipher,
		WrappedDEK: wrapped,
		KeyID:      keyID,
		Week:       week,
	}, nil
}

// Unlock unwraps the service DEK (either from keystore or grid:v1 wrapped token), unlocks
// the encrypted volume on host, and zeroes key material.
func (m *Manager) Unlock(ctx context.Context, req UnlockRequest) (*ProvisionResult, error) {
	res, err := m.unlockVolumeOnly(ctx, req)
	if err != nil {
		return nil, err
	}
	projID := req.ProjectID
	if projID == "" && m.store != nil {
		if rec, gerr := m.store.GetService(res.ServiceID); gerr == nil && rec != nil && rec.ProjectID != "" {
			projID = rec.ProjectID
		}
	}
	_, _ = m.epochs.UnlockService(res.ServiceID, projID, 0, false)
	return res, nil
}

func (m *Manager) unlockVolumeOnly(ctx context.Context, req UnlockRequest) (*ProvisionResult, error) {
	volName, err := sanitizeVolumeName(req.VolumeName)
	if err != nil {
		return nil, err
	}
	if m.epochs.Status().State == epoch.StateLocked {
		return nil, epoch.ErrVaultLocked
	}
	svcID, err := crypto.ValidateIdentifier(resolveVolumeServiceID(volName, req.ServiceID))
	if err != nil {
		return nil, err
	}
	projID := req.ProjectID
	if projID == "" {
		projID = "default"
	}

	var dek []byte
	var keyID string
	var week uint64
	wrapped := strings.TrimSpace(req.WrappedDEK)

	if strings.TrimSpace(req.DEKHex) != "" {
		dek, err = hex.DecodeString(strings.TrimSpace(req.DEKHex))
		if err != nil || len(dek) != crypto.KeySize {
			return nil, errors.New("dek_hex must decode to 32 bytes")
		}
		defer crypto.ZeroBytes(dek)
		st := m.epochs.Status()
		week = st.CurrentWeek
		keyID = req.KeyID
	} else {
		if wrapped == "" && m.store != nil {
			if rec, gerr := m.store.GetService(svcID); gerr == nil && rec != nil {
				if vrec, ok := rec.VolumeDEKs[volName]; ok && vrec.WrappedDEK != "" {
					wrapped = vrec.WrappedDEK
				} else {
					wrapped = rec.WrappedDEK
				}
				if rec.ProjectID != "" {
					projID = rec.ProjectID
				}
			}
		}
		if wrapped == "" {
			return nil, errors.New("wrapped_dek or keystore record required to unlock volume")
		}
		parsed, err := crypto.ParseToken(wrapped)
		if err != nil {
			return nil, fmt.Errorf("invalid wrapped_dek token: %w", err)
		}
		if parsed.ServiceID != svcID {
			return nil, fmt.Errorf("volume token belongs to service %s, not %s", parsed.ServiceID, svcID)
		}
		sek, err := m.epochs.SEKForWeekOrFetch(projID, svcID, parsed.Week)
		if err != nil {
			return nil, err
		}
		defer crypto.ZeroBytes(sek)
		dek, _, err = crypto.UnwrapDEK(sek, wrapped)
		if err != nil {
			return nil, err
		}
		defer crypto.ZeroBytes(dek)
		keyID = parsed.KeyID
		week = parsed.Week
	}

	hostPath, cipher, err := m.unlockHostVolume(ctx, volName, dek, req.OwnerUID, req.OwnerGID)
	if err != nil {
		return nil, err
	}

	return &ProvisionResult{
		OK:         true,
		VolumeName: volName,
		ServiceID:  svcID,
		HostPath:   hostPath,
		Cipher:     cipher,
		WrappedDEK: wrapped,
		KeyID:      keyID,
		Week:       week,
	}, nil
}

// Lock revokes key / locks the encrypted mount for volumeName (keeping unmounted guard 0000).
func (m *Manager) Lock(ctx context.Context, volumeName, serviceID string) error {
	volName, err := sanitizeVolumeName(volumeName)
	if err != nil {
		return err
	}
	svcID := resolveVolumeServiceID(volName, serviceID)
	_ = m.epochs.LockService(svcID, "volume locked")
	return m.lockHostVolume(ctx, volName)
}

// Cleanup deletes an encrypted volume and removes only that volume's entry from multi-volume services.
func (m *Manager) Cleanup(ctx context.Context, volumeName, serviceID string) error {
	volName, err := sanitizeVolumeName(volumeName)
	if err != nil {
		return err
	}
	svcID := resolveVolumeServiceID(volName, serviceID)
	if err := m.cleanupHostVolume(ctx, volName); err != nil {
		return err
	}
	if m.store != nil {
		if svcID == "vol-"+volName {
			_ = m.store.DeleteService(svcID)
		} else if rec, gerr := m.store.GetService(svcID); gerr == nil && rec != nil {
			filtered := rec.VolumeNames[:0]
			for _, vn := range rec.VolumeNames {
				if vn != "" && vn != volName {
					filtered = append(filtered, vn)
				}
			}
			rec.VolumeNames = filtered
			if rec.VolumeDEKs != nil {
				delete(rec.VolumeDEKs, volName)
			}
			if rec.WrappedDEK == "" && len(rec.VolumeNames) == 0 && len(rec.VolumeDEKs) == 0 && !rec.OptedIn {
				_ = m.store.DeleteService(svcID)
			} else {
				_ = m.store.SaveService(rec)
			}
		}
	}
	return nil
}

func serviceVolumeNames(rec *crypto.ServiceKeyRecord) []string {
	if rec == nil {
		return nil
	}
	seen := make(map[string]struct{}, len(rec.VolumeNames)+len(rec.VolumeDEKs))
	out := make([]string, 0, len(rec.VolumeNames)+len(rec.VolumeDEKs))
	for _, vn := range rec.VolumeNames {
		if vn == "" {
			continue
		}
		if _, ok := seen[vn]; !ok {
			seen[vn] = struct{}{}
			out = append(out, vn)
		}
	}
	for vn := range rec.VolumeDEKs {
		if vn == "" {
			continue
		}
		if _, ok := seen[vn]; !ok {
			seen[vn] = struct{}{}
			out = append(out, vn)
		}
	}
	return out
}

// UnlockServiceVolumes unlocks/remounts every persisted encrypted volume registered to serviceID.
func (m *Manager) UnlockServiceVolumes(ctx context.Context, serviceID string) error {
	if m.store == nil {
		return nil
	}
	rec, err := m.store.GetService(serviceID)
	if err != nil || rec == nil {
		return err
	}
	for _, vn := range serviceVolumeNames(rec) {
		vrec, hasVRec := rec.VolumeDEKs[vn]
		uid := rec.OwnerUID
		gid := rec.OwnerGID
		wrapped := rec.WrappedDEK
		keyID := rec.CurrentKeyID
		if hasVRec {
			if vrec.OwnerUID != 0 {
				uid = vrec.OwnerUID
			}
			if vrec.OwnerGID != 0 {
				gid = vrec.OwnerGID
			}
			if vrec.WrappedDEK != "" {
				wrapped = vrec.WrappedDEK
			}
			if vrec.KeyID != "" {
				keyID = vrec.KeyID
			}
		}
		if _, uerr := m.unlockVolumeOnly(ctx, UnlockRequest{
			VolumeName: vn,
			ServiceID:  rec.ServiceID,
			ProjectID:  rec.ProjectID,
			WrappedDEK: wrapped,
			KeyID:      keyID,
			OwnerUID:   uid,
			OwnerGID:   gid,
		}); uerr != nil {
			return fmt.Errorf("unlock volume %s for %s: %w", vn, serviceID, uerr)
		}
	}
	return nil
}

// LockServiceVolumes locks every persisted encrypted volume registered to serviceID.
func (m *Manager) LockServiceVolumes(ctx context.Context, serviceID string) int {
	if m.store == nil {
		return 0
	}
	rec, err := m.store.GetService(serviceID)
	if err != nil || rec == nil {
		return 0
	}
	locked := 0
	for _, vn := range serviceVolumeNames(rec) {
		if m.lockHostVolume(ctx, vn) == nil {
			locked++
		}
	}
	return locked
}

// LockAll locks all persisted encrypted host volumes across every service (used during lockdown).
func (m *Manager) LockAll(ctx context.Context) int {
	if m.store == nil {
		return 0
	}
	recs, err := m.store.ListServices()
	if err != nil {
		return 0
	}
	locked := 0
	for _, rec := range recs {
		for _, vn := range serviceVolumeNames(rec) {
			if m.lockHostVolume(ctx, vn) == nil {
				locked++
			}
		}
	}
	return locked
}

func (m *Manager) mountDir(volName string) string {
	return filepath.Join(m.cfg.BaseDir, volName)
}

func parseScriptResult(out []byte, defaultCipher string) (string, string, error) {
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var parsed struct {
			HostPath string `json:"host_path"`
			Cipher   string `json:"cipher"`
			Backend  string `json:"backend"`
		}
		if err := json.Unmarshal([]byte(line), &parsed); err == nil && parsed.HostPath != "" {
			c := parsed.Cipher
			if c == "" {
				c = parsed.Backend
			}
			if c == "" {
				c = defaultCipher
			}
			return parsed.HostPath, c, nil
		}
	}
	return "", "", fmt.Errorf("volume helper did not return JSON host_path (output=%q)", strings.TrimSpace(string(out)))
}

func (m *Manager) provisionHostVolume(ctx context.Context, volName string, dek []byte, uid, gid int) (string, string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	mountDir := m.mountDir(volName)
	dataDir := filepath.Join(mountDir, "data")

	if m.cfg.Backend == "mock" && m.cfg.AllowMock {
		if err := os.MkdirAll(dataDir, 0o750); err != nil {
			return "", "", err
		}
		if err := os.Chmod(mountDir, 0o755); err != nil {
			return "", "", err
		}
		return dataDir, "aes-256-xts-mock", nil
	}

	cctx, cancel := context.WithTimeout(ctx, m.cfg.CmdTimeout)
	defer cancel()

	if m.cfg.ScriptPath != "" {
		stdinHex := []byte(hex.EncodeToString(dek) + "\n")
		args := []string{"provision", volName}
		if m.cfg.Backend != "" && m.cfg.Backend != "auto" {
			args = append(args, "--backend", m.cfg.Backend)
		}
		if uid > 0 {
			args = append(args, "--uid", strconv.Itoa(uid))
		}
		if gid > 0 {
			args = append(args, "--gid", strconv.Itoa(gid))
		}
		out, err := m.runner.Run(cctx, stdinHex, m.cfg.ScriptPath, args...)
		crypto.ZeroBytes(stdinHex)
		if err != nil {
			return "", "", fmt.Errorf("volume provision script failed: %w (%s)", err, strings.TrimSpace(string(out)))
		}
		hostPath, cipher, perr := parseScriptResult(out, "fscrypt-v2")
		if perr != nil {
			return "", "", perr
		}
		if uid > 0 || gid > 0 {
			_ = os.Chown(hostPath, uid, gid)
		}
		return hostPath, cipher, nil
	}

	return "", "", errors.New("volume backend disabled or scripts/smsly-volume-crypt.sh not configured")
}

func (m *Manager) unlockHostVolume(ctx context.Context, volName string, dek []byte, uid, gid int) (string, string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	mountDir := m.mountDir(volName)
	dataDir := filepath.Join(mountDir, "data")

	if m.cfg.Backend == "mock" && m.cfg.AllowMock {
		_ = os.MkdirAll(dataDir, 0o750)
		_ = os.Chmod(mountDir, 0o755)
		return dataDir, "aes-256-xts-mock", nil
	}

	cctx, cancel := context.WithTimeout(ctx, m.cfg.CmdTimeout)
	defer cancel()

	if m.cfg.ScriptPath != "" {
		stdinHex := []byte(hex.EncodeToString(dek) + "\n")
		args := []string{"unlock", volName}
		if m.cfg.Backend != "" && m.cfg.Backend != "auto" {
			args = append(args, "--backend", m.cfg.Backend)
		}
		out, err := m.runner.Run(cctx, stdinHex, m.cfg.ScriptPath, args...)
		crypto.ZeroBytes(stdinHex)
		if err != nil {
			return "", "", fmt.Errorf("volume unlock script failed: %w (%s)", err, strings.TrimSpace(string(out)))
		}
		hostPath, cipher, perr := parseScriptResult(out, "fscrypt-v2")
		if perr != nil {
			return "", "", perr
		}
		if uid > 0 || gid > 0 {
			_ = os.Chown(hostPath, uid, gid)
		}
		return hostPath, cipher, nil
	}

	return "", "", errors.New("volume unlock backend not configured")
}

func (m *Manager) lockHostVolume(ctx context.Context, volName string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	mountDir := m.mountDir(volName)
	if m.cfg.Backend == "mock" && m.cfg.AllowMock {
		return os.Chmod(mountDir, 0o000)
	}
	cctx, cancel := context.WithTimeout(ctx, m.cfg.CmdTimeout)
	defer cancel()

	if m.cfg.ScriptPath != "" {
		out, err := m.runner.Run(cctx, nil, m.cfg.ScriptPath, "lock", volName)
		if _, statErr := os.Stat(mountDir); statErr == nil {
			_ = os.Chmod(mountDir, 0o000)
		}
		if err != nil {
			return fmt.Errorf("volume lock failed: %w (%s)", err, strings.TrimSpace(string(out)))
		}
		return nil
	}
	if _, statErr := os.Stat(mountDir); statErr == nil {
		_ = os.Chmod(mountDir, 0o000)
	}
	return nil
}

func (m *Manager) cleanupHostVolume(ctx context.Context, volName string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	mountDir := m.mountDir(volName)
	if m.cfg.Backend == "mock" && m.cfg.AllowMock {
		_ = os.Chmod(mountDir, 0o700)
		return os.RemoveAll(mountDir)
	}
	cctx, cancel := context.WithTimeout(ctx, m.cfg.CmdTimeout)
	defer cancel()

	if m.cfg.ScriptPath != "" {
		out, err := m.runner.Run(cctx, nil, m.cfg.ScriptPath, "cleanup", volName)
		if err != nil {
			return fmt.Errorf("volume cleanup failed: %w (%s)", err, strings.TrimSpace(string(out)))
		}
		return nil
	}
	return errors.New("volume cleanup backend not configured")
}
