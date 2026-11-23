package crypto

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"errors"
	"fmt"
	"hash"
	"io"
	"strings"
)

const (
	// ControlPlaneServiceID is the reserved service identifier for the PaaS itself.
	ControlPlaneServiceID = "paas-control-plane"

	// KeySize is the 256-bit key size used across SMK, SEK, and DEK keys.
	KeySize = 32
)

// HKDFExtract implements RFC 5869 HKDF-Extract with SHA-256.
func HKDFExtract(salt, ikm []byte) []byte {
	if len(salt) == 0 {
		salt = make([]byte, sha256.Size)
	}
	mac := hmac.New(sha256.New, salt)
	_, _ = mac.Write(ikm)
	return mac.Sum(nil)
}

// HKDFExpand implements RFC 5869 HKDF-Expand with SHA-256.
func HKDFExpand(prk, info []byte, length int) ([]byte, error) {
	if length <= 0 || length > 255*sha256.Size {
		return nil, fmt.Errorf("invalid HKDF output length %d", length)
	}
	var out []byte
	var prev []byte
	counter := byte(1)
	var mac hash.Hash

	for len(out) < length {
		mac = hmac.New(sha256.New, prk)
		_, _ = mac.Write(prev)
		_, _ = mac.Write(info)
		_, _ = mac.Write([]byte{counter})
		prev = mac.Sum(nil)
		out = append(out, prev...)
		counter++
	}
	return out[:length], nil
}

// DeriveHKDF derives a cryptographic key using RFC 5869 HKDF-SHA256.
func DeriveHKDF(ikm, salt, info []byte, length int) ([]byte, error) {
	if len(ikm) == 0 {
		return nil, errors.New("HKDF input keying material cannot be empty")
	}
	prk := HKDFExtract(salt, ikm)
	defer ZeroBytes(prk)
	return HKDFExpand(prk, info, length)
}

// ValidateIdentifier ensures IDs embedded in key derivation context and token strings
// cannot inject delimiters.
func ValidateIdentifier(id string) (string, error) {
	trimmed := strings.TrimSpace(id)
	if trimmed == "" {
		return "", errors.New("identifier cannot be empty")
	}
	if trimmed == "." || strings.Contains(trimmed, "..") {
		return "", fmt.Errorf("invalid path sequence in identifier %q", id)
	}
	if len(trimmed) > 128 {
		return "", errors.New("identifier exceeds 128 characters")
	}
	for i := 0; i < len(trimmed); i++ {
		c := trimmed[i]
		valid := (c >= 'a' && c <= 'z') ||
			(c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') ||
			c == '-' || c == '_' || c == '.'
		if !valid {
			return "", fmt.Errorf("invalid character %q in identifier", c)
		}
	}
	return trimmed, nil
}

// DeriveWeeklySMK derives the week-scoped Sub-Master Key (SMK_w) from the root Master Key (MK).
// This is executed either on the external Root Vault (preferred) or transiently in locked RAM.
func DeriveWeeklySMK(masterKey []byte, week uint64, clusterSalt string) ([]byte, error) {
	if len(masterKey) < 16 {
		return nil, errors.New("master key must be at least 16 bytes")
	}
	salt := []byte("trulay-grid|mk-salt|v1|" + strings.TrimSpace(clusterSalt))
	info := []byte(fmt.Sprintf("trulay-grid|smk|week:%d", week))
	return DeriveHKDF(masterKey, salt, info, KeySize)
}

// DeriveServiceSEK derives a unique 256-bit Service Encryption Key (SEK_{s,w}) from SMK_w.
// The PaaS control plane uses a completely isolated domain separator from tenant workloads.
func DeriveServiceSEK(smk []byte, projectID, serviceID string, week uint64) ([]byte, error) {
	if len(smk) != KeySize {
		return nil, fmt.Errorf("SMK must be %d bytes", KeySize)
	}
	svc, err := ValidateIdentifier(serviceID)
	if err != nil {
		return nil, fmt.Errorf("invalid service_id: %w", err)
	}

	proj := strings.TrimSpace(projectID)
	if proj == "" {
		proj = "default"
	} else {
		proj, err = ValidateIdentifier(proj)
		if err != nil {
			return nil, fmt.Errorf("invalid project_id: %w", err)
		}
	}

	salt := []byte("trulay-grid|smk-salt|v1")
	var info []byte
	if svc == ControlPlaneServiceID {
		info = []byte(fmt.Sprintf("trulay-grid|sek|control-plane|week:%d", week))
	} else {
		info = []byte(fmt.Sprintf("trulay-grid|sek|tenant|week:%d|project:%s|service:%s", week, proj, svc))
	}
	return DeriveHKDF(smk, salt, info, KeySize)
}

// DerivePurposeSubkey domain-separates purpose keys from a service SEK (transit, redis, wrap, backup).
func DerivePurposeSubkey(sek []byte, purpose string) ([]byte, error) {
	if len(sek) != KeySize {
		return nil, fmt.Errorf("SEK must be %d bytes", KeySize)
	}
	cleanPurpose, err := ValidateIdentifier(purpose)
	if err != nil {
		return nil, fmt.Errorf("invalid purpose: %w", err)
	}
	info := []byte("trulay-grid|purpose|v1|" + cleanPurpose)
	return DeriveHKDF(sek, []byte("trulay-grid|sek-subkey|v1"), info, KeySize)
}

// HKDFReader returns an io.Reader stream if needed by callers.
func HKDFReader(ikm, salt, info []byte) (io.Reader, error) {
	out, err := DeriveHKDF(ikm, salt, info, KeySize)
	if err != nil {
		return nil, err
	}
	return bytes.NewReader(out), nil
}
