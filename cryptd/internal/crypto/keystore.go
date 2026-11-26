package crypto

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// EpochStateFile persists monotonic epoch floor across restarts so rollback to old weeks is rejected.
type EpochStateFile struct {
	MinValidWeek    uint64    `json:"min_valid_week"`
	CurrentWeek     uint64    `json:"current_week"`
	EpochAnchorUnix int64     `json:"epoch_anchor_unix"`
	LastSyncAt      time.Time `json:"last_sync_at"`
}

// VolumeDEKRecord stores the wrapped DEK and mount metadata for a single encrypted host volume.
type VolumeDEKRecord struct {
	VolumeName string    `json:"volume_name"`
	WrappedDEK string    `json:"wrapped_dek"`
	WrapWeek   uint64    `json:"wrap_week"`
	KeyID      string    `json:"key_id,omitempty"`
	Cipher     string    `json:"cipher,omitempty"`
	HostPath   string    `json:"host_path,omitempty"`
	OwnerUID   int       `json:"owner_uid,omitempty"`
	OwnerGID   int       `json:"owner_gid,omitempty"`
	RotatedAt  time.Time `json:"rotated_at,omitempty"`
}

// ServiceKeyRecord persists per-service non-secret metadata and wrapped persistent DEKs.
// Raw DEKs, SEKs, SMKs, and Master Keys are NEVER written to disk.
type ServiceKeyRecord struct {
	ServiceID        string                     `json:"service_id"`
	ProjectID        string                     `json:"project_id"`
	OptedIn          bool                       `json:"opted_in"`
	DBRoles          []string                   `json:"db_roles,omitempty"`
	DBNames          []string                   `json:"db_names,omitempty"`
	RedisUsers       []string                   `json:"redis_users,omitempty"`
	RedisPrefixes    []string                   `json:"redis_prefixes,omitempty"`
	UpstreamPG       string                     `json:"upstream_pg,omitempty"`
	UpstreamRedis    string                     `json:"upstream_redis,omitempty"`
	UpstreamRedisTLS bool                       `json:"upstream_redis_tls,omitempty"`
	EncryptRedisVals bool                       `json:"encrypt_redis_vals"`
	VolumeNames      []string                   `json:"volume_names,omitempty"`
	VolumeDEKs       map[string]VolumeDEKRecord `json:"volume_deks,omitempty"`
	Cipher           string                     `json:"cipher,omitempty"`
	OwnerUID         int                        `json:"owner_uid,omitempty"`
	OwnerGID         int                        `json:"owner_gid,omitempty"`
	CurrentKeyID     string                     `json:"current_key_id,omitempty"`
	WrappedDEK       string                     `json:"wrapped_dek,omitempty"`
	WrapWeek         uint64                     `json:"wrap_week,omitempty"`
	Locked           bool                       `json:"locked"`
	LeasePersistent  bool                       `json:"lease_persistent,omitempty"`
	LeaseExpiresAt   time.Time                  `json:"lease_expires_at,omitempty"`
	CreatedAt        time.Time                  `json:"created_at"`
	RotatedAt        time.Time                  `json:"rotated_at"`
}

// KeyStore manages persistent state and wrapped service DEKs with 0700/0600 permissions.
type KeyStore struct {
	mu      sync.RWMutex
	rootDir string
}

// OpenKeyStore initializes keystore directories atomically with restrictive permissions.
func OpenKeyStore(rootDir string) (*KeyStore, error) {
	if rootDir == "" {
		return nil, errors.New("keystore root directory cannot be empty")
	}
	svcDir := filepath.Join(rootDir, "services")
	if err := os.MkdirAll(svcDir, 0o700); err != nil {
		return nil, fmt.Errorf("create keystore dir: %w", err)
	}
	_ = os.Chmod(rootDir, 0o700)
	_ = os.Chmod(svcDir, 0o700)
	return &KeyStore{rootDir: rootDir}, nil
}

func writeFileAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	cleanup := func() {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
	}
	if err := tmp.Chmod(0o600); err != nil {
		cleanup()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
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

// LoadEpochState loads the persisted monotonic epoch floor, or nil if first boot.
func (ks *KeyStore) LoadEpochState() (*EpochStateFile, error) {
	ks.mu.RLock()
	defer ks.mu.RUnlock()

	p := filepath.Join(ks.rootDir, "epoch_state.json")
	raw, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var st EpochStateFile
	if err := json.Unmarshal(raw, &st); err != nil {
		return nil, err
	}
	return &st, nil
}

// SaveEpochState persists epoch metadata atomically.
func (ks *KeyStore) SaveEpochState(st *EpochStateFile) error {
	if st == nil {
		return errors.New("nil epoch state")
	}
	ks.mu.Lock()
	defer ks.mu.Unlock()

	raw, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(ks.rootDir, "epoch_state.json"), raw)
}

func (ks *KeyStore) servicePath(serviceID string) (string, error) {
	clean, err := ValidateIdentifier(serviceID)
	if err != nil {
		return "", err
	}
	return filepath.Join(ks.rootDir, "services", clean+".json"), nil
}

// GetService retrieves a service record if present.
func (ks *KeyStore) GetService(serviceID string) (*ServiceKeyRecord, error) {
	p, err := ks.servicePath(serviceID)
	if err != nil {
		return nil, err
	}
	ks.mu.RLock()
	defer ks.mu.RUnlock()

	raw, err := os.ReadFile(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var rec ServiceKeyRecord
	if err := json.Unmarshal(raw, &rec); err != nil {
		return nil, err
	}
	return &rec, nil
}

// SaveService saves or updates a service record atomically.
func (ks *KeyStore) SaveService(rec *ServiceKeyRecord) error {
	if rec == nil {
		return errors.New("nil service record")
	}
	p, err := ks.servicePath(rec.ServiceID)
	if err != nil {
		return err
	}
	ks.mu.Lock()
	defer ks.mu.Unlock()

	raw, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(p, raw)
}

// UpdateService atomically reads, mutates via fn, and persists a ServiceKeyRecord under the keystore lock.
// If no record exists on disk yet, fn receives a fresh ServiceKeyRecord initialized with the validated ServiceID.
func (ks *KeyStore) UpdateService(serviceID string, fn func(*ServiceKeyRecord) error) error {
	if fn == nil {
		return errors.New("nil update callback")
	}
	cleanID, err := ValidateIdentifier(serviceID)
	if err != nil {
		return err
	}
	p := filepath.Join(ks.rootDir, "services", cleanID+".json")

	ks.mu.Lock()
	defer ks.mu.Unlock()

	var rec *ServiceKeyRecord
	raw, err := os.ReadFile(p)
	if err == nil {
		var loaded ServiceKeyRecord
		if uerr := json.Unmarshal(raw, &loaded); uerr != nil {
			return uerr
		}
		rec = &loaded
	} else if errors.Is(err, os.ErrNotExist) {
		rec = &ServiceKeyRecord{ServiceID: cleanID}
	} else {
		return err
	}

	if err := fn(rec); err != nil {
		return err
	}
	if rec.ServiceID == "" {
		rec.ServiceID = cleanID
	}
	outPath, err := ks.servicePath(rec.ServiceID)
	if err != nil {
		return err
	}
	encoded, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(outPath, encoded)
}

// DeleteService removes a service record from keystore.
func (ks *KeyStore) DeleteService(serviceID string) error {
	p, err := ks.servicePath(serviceID)
	if err != nil {
		return err
	}
	ks.mu.Lock()
	defer ks.mu.Unlock()
	err = os.Remove(p)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}

// ListServices lists all registered service records.
func (ks *KeyStore) ListServices() ([]*ServiceKeyRecord, error) {
	ks.mu.RLock()
	defer ks.mu.RUnlock()

	svcDir := filepath.Join(ks.rootDir, "services")
	entries, err := os.ReadDir(svcDir)
	if err != nil {
		return nil, err
	}
	var out []*ServiceKeyRecord
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(svcDir, entry.Name()))
		if err != nil {
			continue
		}
		var rec ServiceKeyRecord
		if json.Unmarshal(raw, &rec) == nil {
			out = append(out, &rec)
		}
	}
	return out, nil
}

// WrapDEK wraps a raw 32-byte persistent volume/data DEK into `grid:v1:<week>:<service_id>:<key_id>:<b64url>`
// using the `volume-wrap` subkey derived from SEK_{service,week}.
func WrapDEK(sek []byte, week uint64, serviceID, keyID string, rawDEK []byte) (string, error) {
	if len(rawDEK) == 0 {
		return "", errors.New("cannot wrap empty DEK")
	}
	svc, err := ValidateIdentifier(serviceID)
	if err != nil {
		return "", err
	}
	if keyID == "" {
		keyID = DefaultKeyID
	}
	kid, err := ValidateIdentifier(keyID)
	if err != nil {
		return "", err
	}
	subkey, err := DerivePurposeSubkey(sek, "volume-wrap")
	if err != nil {
		return "", err
	}
	defer ZeroBytes(subkey)

	aad := FormatAAD(week, svc, kid, "volume-dek-wrap")
	blob, err := EncryptAEAD(subkey, rawDEK, aad)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("grid:v1:%d:%s:%s:%s", week, svc, kid, base64.RawURLEncoding.EncodeToString(blob)), nil
}

// UnwrapDEK recovers a raw DEK wrapped by WrapDEK; caller MUST ZeroBytes(dek).
func UnwrapDEK(sek []byte, token string) ([]byte, *ParsedToken, error) {
	parsed, err := ParseToken(token)
	if err != nil {
		return nil, nil, err
	}
	subkey, err := DerivePurposeSubkey(sek, "volume-wrap")
	if err != nil {
		return nil, nil, err
	}
	defer ZeroBytes(subkey)

	aad := FormatAAD(parsed.Week, parsed.ServiceID, parsed.KeyID, "volume-dek-wrap")
	dek, err := DecryptAEAD(subkey, parsed.Ciphertext, aad)
	if err != nil {
		return nil, nil, err
	}
	return dek, parsed, nil
}
