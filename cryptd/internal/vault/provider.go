package vault

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
)

// SMKResult represents the weekly Sub-Master Key fetched or derived from the external vault.
type SMKResult struct {
	Week       uint64
	SMK        []byte
	Provider   string
	KeyVersion string
	FetchedAt  time.Time
}

// Provider abstracts every supported external Root/Master Key Vault backend.
type Provider interface {
	Name() string
	FetchWeeklySMK(ctx context.Context, week uint64, clusterSalt string) (*SMKResult, error)
	Health(ctx context.Context) error
}

// Config configures which external vault backend grid-cryptd connects to.
type Config struct {
	ProviderName string
	Endpoint     string
	NodeID       string
	ClusterSalt  string
	Timeout      time.Duration

	// mTLS / TLS verification options (shared by http_vault, hashicorp, infisical, cloud_kms).
	CAFile             string
	ClientCertFile     string
	ClientKeyFile      string
	InsecureSkipVerify bool

	// http_vault (Trulay External Go Root Vault)
	HMACSecret string
	AuthToken  string

	// hashicorp / openbao
	VaultNamespace  string
	VaultMount      string // e.g. "secret" for KV v2, "transit" for Transit
	VaultKVPath     string // e.g. "trulay-grid/master-key"
	VaultKVField    string // default "master_key"
	VaultTransitKey string // if non-empty, uses /v1/<mount>/hmac/<key>/sha2-256
	VaultRoleID     string
	VaultSecretID   string

	// infisical
	InfisicalToken        string
	InfisicalClientID     string
	InfisicalClientSecret string
	InfisicalWorkspaceID  string
	InfisicalEnvironment  string
	InfisicalSecretName   string
	InfisicalSecretPath   string

	// cloud_kms (AWS/GCP/Generic KMS envelope decrypt or GenerateMac endpoint)
	KMSMode           string // "hmac" or "decrypt"
	KMSKeyID          string
	KMSCiphertextB64  string
	KMSAuthHeaderName string
	KMSAuthCmd        string // optional command yielding short-lived bearer token on stdout

	// command (external CLI / HSM / SOPS / 1Password / Bitwarden bridge)
	CommandBin  string
	CommandArgs []string

	// dev_memory (explicit dev/test only)
	AllowDevVault bool
	DevMasterKey  string
}

// BuildHTTPClient builds an HTTP client honoring optional custom CA and client mTLS certificates.
func BuildHTTPClient(cfg Config) (*http.Client, error) {
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	tlsCfg := &tls.Config{
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: cfg.InsecureSkipVerify, //nolint:gosec // explicit operator override
	}
	if cfg.CAFile != "" {
		caPEM, err := os.ReadFile(cfg.CAFile)
		if err != nil {
			return nil, fmt.Errorf("read vault CA file: %w", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caPEM) {
			return nil, errors.New("invalid vault CA PEM")
		}
		tlsCfg.RootCAs = pool
	}
	if cfg.ClientCertFile != "" && cfg.ClientKeyFile != "" {
		cert, err := tls.LoadX509KeyPair(cfg.ClientCertFile, cfg.ClientKeyFile)
		if err != nil {
			return nil, fmt.Errorf("load vault mTLS client keypair: %w", err)
		}
		tlsCfg.Certificates = []tls.Certificate{cert}
	}
	tr := &http.Transport{
		TLSClientConfig:     tlsCfg,
		TLSHandshakeTimeout: 10 * time.Second,
	}
	return &http.Client{
		Timeout:   timeout,
		Transport: tr,
	}, nil
}

// DecodeKeyMaterial decodes a master key or SMK from hex, base64/base64url, or prefixed string.
// Returns an independent byte slice that the caller must ZeroBytes after use.
func DecodeKeyMaterial(raw string) ([]byte, error) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return nil, errors.New("empty key material")
	}
	if strings.HasPrefix(s, "hex:") {
		return hex.DecodeString(strings.TrimSpace(strings.TrimPrefix(s, "hex:")))
	}
	if strings.HasPrefix(s, "b64:") {
		s = strings.TrimSpace(strings.TrimPrefix(s, "b64:"))
	} else if len(s) == 64 || len(s) == 128 {
		if b, err := hex.DecodeString(s); err == nil {
			return b, nil
		}
	}
	for _, enc := range []*base64.Encoding{
		base64.StdEncoding,
		base64.RawStdEncoding,
		base64.URLEncoding,
		base64.RawURLEncoding,
	} {
		if b, err := enc.DecodeString(s); err == nil && len(b) >= 16 {
			return b, nil
		}
	}
	if len(s) >= 32 {
		return []byte(s), nil
	}
	return nil, errors.New("unsupported or too-short key material format")
}

// NewProvider constructs the configured external vault provider.
func NewProvider(cfg Config) (Provider, error) {
	name := strings.ToLower(strings.TrimSpace(cfg.ProviderName))
	if name == "" {
		return nil, errors.New("CRYPTD_VAULT_PROVIDER is required (fail-closed: no silent default)")
	}
	switch name {
	case "http_vault", "root_vault", "custom_http":
		return NewHTTPRootVaultProvider(cfg)
	case "hashicorp", "vault", "openbao":
		return NewHashiCorpProvider(cfg)
	case "infisical":
		return NewInfisicalProvider(cfg)
	case "cloud_kms", "kms", "aws_kms", "gcp_kms":
		return NewCloudKMSProvider(cfg)
	case "command", "cli", "hsm_cmd":
		return NewCommandProvider(cfg)
	case "dev_memory", "memory":
		return NewDevMemoryProvider(cfg)
	default:
		return nil, fmt.Errorf("unsupported vault provider %q", cfg.ProviderName)
	}
}

func deriveSMKAndZeroMK(mk []byte, week uint64, clusterSalt, provider, version string) (*SMKResult, error) {
	defer crypto.ZeroBytes(mk)
	smk, err := crypto.DeriveWeeklySMK(mk, week, clusterSalt)
	if err != nil {
		return nil, err
	}
	return &SMKResult{
		Week:       week,
		SMK:        smk,
		Provider:   provider,
		KeyVersion: version,
		FetchedAt:  time.Now().UTC(),
	}, nil
}
