package crypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"runtime"
	"strconv"
	"strings"
)

const (
	// TokenPrefix is the versioned envelope prefix used for DB/control-plane transit ciphertext.
	TokenPrefix = "grid:v1:"

	// RedisValuePrefix is the compact envelope prefix stored inside Redis values.
	RedisValuePrefix = "GRD1:"

	// DefaultKeyID identifies the default per-service envelope key version.
	DefaultKeyID = "k1"
)

// ZeroBytes overwrites sensitive byte slices in RAM and hints the runtime not to elide it.
func ZeroBytes(b []byte) {
	for i := range b {
		b[i] = 0
	}
	runtime.KeepAlive(b)
}

// GenerateRandomBytes returns n cryptographically random bytes.
func GenerateRandomBytes(n int) ([]byte, error) {
	b := make([]byte, n)
	if _, err := io.ReadFull(rand.Reader, b); err != nil {
		return nil, err
	}
	return b, nil
}

// EncryptAEAD encrypts plaintext under AES-256-GCM with associated data aad.
// Output format: 12-byte nonce || ciphertext || 16-byte GCM tag.
func EncryptAEAD(key, plaintext, aad []byte) ([]byte, error) {
	if len(key) != KeySize {
		return nil, fmt.Errorf("AEAD key must be %d bytes", KeySize)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	sealed := gcm.Seal(nonce, nonce, plaintext, aad)
	return sealed, nil
}

// DecryptAEAD decrypts an EncryptAEAD blob with AES-256-GCM and verifies aad.
func DecryptAEAD(key, blob, aad []byte) ([]byte, error) {
	if len(key) != KeySize {
		return nil, fmt.Errorf("AEAD key must be %d bytes", KeySize)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonceSize := gcm.NonceSize()
	if len(blob) < nonceSize+gcm.Overhead() {
		return nil, errors.New("ciphertext too short")
	}
	nonce := blob[:nonceSize]
	ciphertext := blob[nonceSize:]
	return gcm.Open(nil, nonce, ciphertext, aad)
}

// ParsedToken holds decoded components of a grid:v1 token.
type ParsedToken struct {
	Week       uint64
	ServiceID  string
	KeyID      string
	Ciphertext []byte
}

// FormatAAD builds canonical associated data for transit AEAD tokens.
func FormatAAD(week uint64, serviceID, keyID, contextStr string) []byte {
	return []byte(fmt.Sprintf("grid:v1|%d|%s|%s|%s", week, serviceID, keyID, contextStr))
}

// EncryptToken produces a portable `grid:v1:<week>:<service_id>:<key_id>:<b64url>` ciphertext.
func EncryptToken(sek []byte, week uint64, serviceID, keyID, contextStr string, plaintext []byte) (string, error) {
	if keyID == "" {
		keyID = DefaultKeyID
	}
	svc, err := ValidateIdentifier(serviceID)
	if err != nil {
		return "", err
	}
	kid, err := ValidateIdentifier(keyID)
	if err != nil {
		return "", err
	}
	subkey, err := DerivePurposeSubkey(sek, "transit-aead")
	if err != nil {
		return "", err
	}
	defer ZeroBytes(subkey)

	aad := FormatAAD(week, svc, kid, contextStr)
	blob, err := EncryptAEAD(subkey, plaintext, aad)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("grid:v1:%d:%s:%s:%s", week, svc, kid, base64.RawURLEncoding.EncodeToString(blob)), nil
}

// ParseToken decodes a grid:v1 token without decrypting it.
func ParseToken(token string) (*ParsedToken, error) {
	if !strings.HasPrefix(token, TokenPrefix) {
		return nil, errors.New("token does not start with grid:v1:")
	}
	rest := strings.TrimPrefix(token, TokenPrefix)
	parts := strings.SplitN(rest, ":", 4)
	if len(parts) != 4 {
		return nil, errors.New("malformed grid:v1 token")
	}
	week, err := strconv.ParseUint(parts[0], 10, 64)
	if err != nil {
		return nil, fmt.Errorf("invalid week in token: %w", err)
	}
	svc, err := ValidateIdentifier(parts[1])
	if err != nil {
		return nil, fmt.Errorf("invalid service_id in token: %w", err)
	}
	kid, err := ValidateIdentifier(parts[2])
	if err != nil {
		return nil, fmt.Errorf("invalid key_id in token: %w", err)
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[3])
	if err != nil {
		return nil, fmt.Errorf("invalid token base64 payload: %w", err)
	}
	return &ParsedToken{
		Week:       week,
		ServiceID:  svc,
		KeyID:      kid,
		Ciphertext: raw,
	}, nil
}

// DecryptToken verifies AAD and decrypts a parsed grid:v1 token with the matching SEK.
func DecryptToken(sek []byte, parsed *ParsedToken, contextStr string) ([]byte, error) {
	if parsed == nil {
		return nil, errors.New("nil parsed token")
	}
	subkey, err := DerivePurposeSubkey(sek, "transit-aead")
	if err != nil {
		return nil, err
	}
	defer ZeroBytes(subkey)

	aad := FormatAAD(parsed.Week, parsed.ServiceID, parsed.KeyID, contextStr)
	return DecryptAEAD(subkey, parsed.Ciphertext, aad)
}

// EncryptRedisValue encrypts a Redis value bytes slice under service SEK_w into "GRD1:<b64>".
func EncryptRedisValue(sek []byte, week uint64, serviceID string, value []byte) ([]byte, error) {
	subkey, err := DerivePurposeSubkey(sek, "redis-value-aead")
	if err != nil {
		return nil, err
	}
	defer ZeroBytes(subkey)

	aad := []byte(fmt.Sprintf("redis-v1|%s|%d", serviceID, week))
	blob, err := EncryptAEAD(subkey, value, aad)
	if err != nil {
		return nil, err
	}
	frame := make([]byte, 8+len(blob))
	binary.BigEndian.PutUint64(frame[:8], week)
	copy(frame[8:], blob)

	encoded := base64.StdEncoding.EncodeToString(frame)
	out := make([]byte, len(RedisValuePrefix)+len(encoded))
	copy(out, RedisValuePrefix)
	copy(out[len(RedisValuePrefix):], encoded)
	return out, nil
}

// ParseRedisValueWeek extracts the epoch week embedded in a GRD1: Redis value.
func ParseRedisValueWeek(encoded []byte) (uint64, []byte, error) {
	if len(encoded) < len(RedisValuePrefix) || string(encoded[:len(RedisValuePrefix)]) != RedisValuePrefix {
		return 0, nil, errors.New("not a GRD1 encrypted value")
	}
	raw, err := base64.StdEncoding.DecodeString(string(encoded[len(RedisValuePrefix):]))
	if err != nil {
		return 0, nil, fmt.Errorf("invalid GRD1 base64: %w", err)
	}
	if len(raw) < 8+12+16 {
		return 0, nil, errors.New("truncated GRD1 frame")
	}
	week := binary.BigEndian.Uint64(raw[:8])
	return week, raw[8:], nil
}

// DecryptRedisValue decrypts a GRD1: blob extracted from Redis using the matched week SEK.
func DecryptRedisValue(sek []byte, week uint64, serviceID string, blob []byte) ([]byte, error) {
	subkey, err := DerivePurposeSubkey(sek, "redis-value-aead")
	if err != nil {
		return nil, err
	}
	defer ZeroBytes(subkey)

	aad := []byte(fmt.Sprintf("redis-v1|%s|%d", serviceID, week))
	return DecryptAEAD(subkey, blob, aad)
}
