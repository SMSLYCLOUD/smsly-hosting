package vault

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"strings"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
)

// HashiCorpProvider integrates with HashiCorp Vault / OpenBao.
// Supports Transit HMAC (MK stays inside Vault) or KV v2 (transient MK -> HKDF -> ZeroBytes).
type HashiCorpProvider struct {
	cfg    Config
	client *http.Client
}

// NewHashiCorpProvider constructs a HashiCorp Vault / OpenBao provider.
func NewHashiCorpProvider(cfg Config) (*HashiCorpProvider, error) {
	if strings.TrimSpace(cfg.Endpoint) == "" {
		return nil, errors.New("hashicorp vault requires CRYPTD_VAULT_ENDPOINT")
	}
	if cfg.VaultTransitKey == "" && cfg.VaultKVPath == "" {
		return nil, errors.New("hashicorp vault requires VaultTransitKey or VaultKVPath")
	}
	if cfg.AuthToken == "" && (cfg.VaultRoleID == "" || cfg.VaultSecretID == "") {
		return nil, errors.New("hashicorp vault requires AuthToken or AppRole (VaultRoleID + VaultSecretID)")
	}
	if cfg.VaultKVField == "" {
		cfg.VaultKVField = "master_key"
	}
	client, err := BuildHTTPClient(cfg)
	if err != nil {
		return nil, err
	}
	return &HashiCorpProvider{cfg: cfg, client: client}, nil
}

func (p *HashiCorpProvider) Name() string {
	return "hashicorp"
}

func (p *HashiCorpProvider) resolveToken(ctx context.Context) (string, error) {
	if p.cfg.AuthToken != "" {
		return p.cfg.AuthToken, nil
	}
	loginURL := strings.TrimRight(p.cfg.Endpoint, "/") + "/v1/auth/approle/login"
	payload, _ := json.Marshal(map[string]string{
		"role_id":   p.cfg.VaultRoleID,
		"secret_id": p.cfg.VaultSecretID,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, loginURL, bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	if p.cfg.VaultNamespace != "" {
		req.Header.Set("X-Vault-Namespace", p.cfg.VaultNamespace)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("vault approle login HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out struct {
		Auth struct {
			ClientToken string `json:"client_token"`
		} `json:"auth"`
	}
	if err := json.Unmarshal(body, &out); err != nil || out.Auth.ClientToken == "" {
		return "", errors.New("vault approle response missing client_token")
	}
	return out.Auth.ClientToken, nil
}

func (p *HashiCorpProvider) FetchWeeklySMK(ctx context.Context, week uint64, clusterSalt string) (*SMKResult, error) {
	token, err := p.resolveToken(ctx)
	if err != nil {
		return nil, err
	}
	if p.cfg.VaultTransitKey != "" {
		return p.fetchTransitHMAC(ctx, token, week, clusterSalt)
	}
	return p.fetchKVv2(ctx, token, week, clusterSalt)
}

func (p *HashiCorpProvider) fetchTransitHMAC(ctx context.Context, token string, week uint64, clusterSalt string) (*SMKResult, error) {
	mount := p.cfg.VaultMount
	if mount == "" {
		mount = "transit"
	}
	endpoint := fmt.Sprintf("%s/v1/%s/hmac/%s/sha2-256",
		strings.TrimRight(p.cfg.Endpoint, "/"),
		strings.Trim(mount, "/"),
		url.PathEscape(p.cfg.VaultTransitKey),
	)
	inputMsg := fmt.Sprintf("trulay-grid|smk|week:%d|%s", week, strings.TrimSpace(clusterSalt))
	payload, _ := json.Marshal(map[string]string{
		"input": base64.StdEncoding.EncodeToString([]byte(inputMsg)),
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Vault-Token", token)
	if p.cfg.VaultNamespace != "" {
		req.Header.Set("X-Vault-Namespace", p.cfg.VaultNamespace)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("vault transit hmac HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out struct {
		Data struct {
			HMAC string `json:"hmac"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	// Format from Vault is "vault:v1:<base64>"
	parts := strings.SplitN(out.Data.HMAC, ":", 3)
	if len(parts) != 3 {
		return nil, fmt.Errorf("unexpected vault transit hmac format")
	}
	rawHMAC, err := base64.StdEncoding.DecodeString(parts[2])
	if err != nil || len(rawHMAC) < crypto.KeySize {
		return nil, errors.New("invalid vault transit hmac bytes")
	}
	defer crypto.ZeroBytes(rawHMAC)
	smk, err := crypto.DeriveHKDF(rawHMAC, []byte("trulay-grid|vault-transit|v1"), []byte(fmt.Sprintf("week:%d", week)), crypto.KeySize)
	if err != nil {
		return nil, err
	}
	return &SMKResult{
		Week:       week,
		SMK:        smk,
		Provider:   p.Name(),
		KeyVersion: parts[1],
		FetchedAt:  time.Now().UTC(),
	}, nil
}

func (p *HashiCorpProvider) fetchKVv2(ctx context.Context, token string, week uint64, clusterSalt string) (*SMKResult, error) {
	mount := p.cfg.VaultMount
	if mount == "" {
		mount = "secret"
	}
	endpoint := fmt.Sprintf("%s/v1/%s/data/%s",
		strings.TrimRight(p.cfg.Endpoint, "/"),
		strings.Trim(mount, "/"),
		strings.TrimLeft(p.cfg.VaultKVPath, "/"),
	)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Vault-Token", token)
	if p.cfg.VaultNamespace != "" {
		req.Header.Set("X-Vault-Namespace", p.cfg.VaultNamespace)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("vault kv2 HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out struct {
		Data struct {
			Data     map[string]any `json:"data"`
			Metadata struct {
				Version int `json:"version"`
			} `json:"metadata"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	rawVal, _ := out.Data.Data[p.cfg.VaultKVField].(string)
	mk, err := DecodeKeyMaterial(rawVal)
	if err != nil {
		return nil, fmt.Errorf("vault kv2 field %q: %w", p.cfg.VaultKVField, err)
	}
	ver := fmt.Sprintf("kv-v%d", out.Data.Metadata.Version)
	return deriveSMKAndZeroMK(mk, week, clusterSalt, p.Name(), ver)
}

func (p *HashiCorpProvider) Health(ctx context.Context) error {
	endpoint := strings.TrimRight(p.cfg.Endpoint, "/") + "/v1/sys/health"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 500 {
		return fmt.Errorf("vault health status %d", resp.StatusCode)
	}
	return nil
}

// InfisicalProvider fetches Master Key from Infisical (Universal Auth or Service Token).
type InfisicalProvider struct {
	cfg    Config
	client *http.Client
}

// NewInfisicalProvider constructs an Infisical provider.
func NewInfisicalProvider(cfg Config) (*InfisicalProvider, error) {
	if strings.TrimSpace(cfg.Endpoint) == "" {
		cfg.Endpoint = "https://app.infisical.com"
	}
	if cfg.InfisicalSecretName == "" {
		cfg.InfisicalSecretName = "GRID_MASTER_KEY"
	}
	if cfg.InfisicalEnvironment == "" {
		cfg.InfisicalEnvironment = "prod"
	}
	if cfg.InfisicalToken == "" && (cfg.InfisicalClientID == "" || cfg.InfisicalClientSecret == "") {
		return nil, errors.New("infisical requires InfisicalToken or Universal Auth credentials")
	}
	if cfg.InfisicalWorkspaceID == "" {
		return nil, errors.New("infisical requires InfisicalWorkspaceID")
	}
	client, err := BuildHTTPClient(cfg)
	if err != nil {
		return nil, err
	}
	return &InfisicalProvider{cfg: cfg, client: client}, nil
}

func (p *InfisicalProvider) Name() string {
	return "infisical"
}

func (p *InfisicalProvider) resolveBearer(ctx context.Context) (string, error) {
	if p.cfg.InfisicalToken != "" {
		return p.cfg.InfisicalToken, nil
	}
	loginURL := strings.TrimRight(p.cfg.Endpoint, "/") + "/api/v1/auth/universal-auth/login"
	payload, _ := json.Marshal(map[string]string{
		"clientId":     p.cfg.InfisicalClientID,
		"clientSecret": p.cfg.InfisicalClientSecret,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, loginURL, bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := p.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("infisical login HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out struct {
		AccessToken string `json:"accessToken"`
	}
	if err := json.Unmarshal(body, &out); err != nil || out.AccessToken == "" {
		return "", errors.New("infisical login response missing accessToken")
	}
	return out.AccessToken, nil
}

func (p *InfisicalProvider) FetchWeeklySMK(ctx context.Context, week uint64, clusterSalt string) (*SMKResult, error) {
	token, err := p.resolveBearer(ctx)
	if err != nil {
		return nil, err
	}
	q := url.Values{}
	q.Set("workspaceId", p.cfg.InfisicalWorkspaceID)
	q.Set("environment", p.cfg.InfisicalEnvironment)
	if p.cfg.InfisicalSecretPath != "" {
		q.Set("secretPath", p.cfg.InfisicalSecretPath)
	}
	endpoint := fmt.Sprintf("%s/api/v3/secrets/raw/%s?%s",
		strings.TrimRight(p.cfg.Endpoint, "/"),
		url.PathEscape(p.cfg.InfisicalSecretName),
		q.Encode(),
	)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("infisical fetch HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	var out struct {
		Secret struct {
			SecretValue string `json:"secretValue"`
			Version     int    `json:"version"`
		} `json:"secret"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	mk, err := DecodeKeyMaterial(out.Secret.SecretValue)
	if err != nil {
		return nil, fmt.Errorf("infisical secret %q: %w", p.cfg.InfisicalSecretName, err)
	}
	return deriveSMKAndZeroMK(mk, week, clusterSalt, p.Name(), fmt.Sprintf("infisical-v%d", out.Secret.Version))
}

func (p *InfisicalProvider) Health(ctx context.Context) error {
	endpoint := strings.TrimRight(p.cfg.Endpoint, "/") + "/api/status"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 500 {
		return fmt.Errorf("infisical health status %d", resp.StatusCode)
	}
	return nil
}

// CloudKMSProvider supports envelope decrypt or KMS MAC derivation endpoints (GCP/AWS/custom KMS proxy).
type CloudKMSProvider struct {
	cfg    Config
	client *http.Client
}

// NewCloudKMSProvider validates Cloud KMS provider config.
func NewCloudKMSProvider(cfg Config) (*CloudKMSProvider, error) {
	if strings.TrimSpace(cfg.Endpoint) == "" {
		return nil, errors.New("cloud_kms requires CRYPTD_VAULT_ENDPOINT")
	}
	if cfg.KMSMode == "" {
		cfg.KMSMode = "hmac"
	}
	if cfg.KMSMode != "hmac" && cfg.KMSMode != "decrypt" {
		return nil, fmt.Errorf("cloud_kms invalid KMSMode %q (expected hmac or decrypt)", cfg.KMSMode)
	}
	if cfg.KMSMode == "decrypt" && cfg.KMSCiphertextB64 == "" {
		return nil, errors.New("cloud_kms decrypt mode requires KMSCiphertextB64")
	}
	client, err := BuildHTTPClient(cfg)
	if err != nil {
		return nil, err
	}
	return &CloudKMSProvider{cfg: cfg, client: client}, nil
}

func (p *CloudKMSProvider) Name() string {
	return "cloud_kms"
}

func (p *CloudKMSProvider) resolveAuthHeader(ctx context.Context) (string, error) {
	if p.cfg.AuthToken != "" {
		return "Bearer " + p.cfg.AuthToken, nil
	}
	if p.cfg.KMSAuthCmd != "" {
		parts := strings.Fields(p.cfg.KMSAuthCmd)
		if len(parts) > 0 {
			cmd := exec.CommandContext(ctx, parts[0], parts[1:]...)
			out, err := cmd.Output()
			if err != nil {
				return "", fmt.Errorf("kms auth command failed: %w", err)
			}
			tok := strings.TrimSpace(string(out))
			if tok != "" {
				return "Bearer " + tok, nil
			}
		}
	}
	return "", nil
}

func (p *CloudKMSProvider) FetchWeeklySMK(ctx context.Context, week uint64, clusterSalt string) (*SMKResult, error) {
	var bodyObj map[string]any
	if p.cfg.KMSMode == "hmac" {
		data := base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("trulay-grid|smk|week:%d|%s", week, clusterSalt)))
		bodyObj = map[string]any{
			"key_id": p.cfg.KMSKeyID,
			"data":   data,
		}
	} else {
		bodyObj = map[string]any{
			"key_id":     p.cfg.KMSKeyID,
			"ciphertext": p.cfg.KMSCiphertextB64,
		}
	}
	payload, _ := json.Marshal(bodyObj)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.cfg.Endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if authVal, err := p.resolveAuthHeader(ctx); err != nil {
		return nil, err
	} else if authVal != "" {
		hdr := p.cfg.KMSAuthHeaderName
		if hdr == "" {
			hdr = "Authorization"
		}
		req.Header.Set(hdr, authVal)
	}

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("cloud_kms HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(respBody)))
	}
	var out map[string]any
	if err := json.Unmarshal(respBody, &out); err != nil {
		return nil, err
	}

	if p.cfg.KMSMode == "hmac" {
		macB64 := firstString(out, "mac", "Mac", "mac_b64")
		raw, err := base64.StdEncoding.DecodeString(macB64)
		if err != nil || len(raw) < crypto.KeySize {
			return nil, errors.New("cloud_kms hmac response missing valid mac")
		}
		defer crypto.ZeroBytes(raw)
		smk, err := crypto.DeriveHKDF(raw, []byte("trulay-grid|kms-hmac|v1"), []byte(fmt.Sprintf("week:%d", week)), crypto.KeySize)
		if err != nil {
			return nil, err
		}
		return &SMKResult{
			Week:       week,
			SMK:        smk,
			Provider:   p.Name(),
			KeyVersion: firstString(out, "name", "KeyId", "key_version"),
			FetchedAt:  time.Now().UTC(),
		}, nil
	}

	ptB64 := firstString(out, "plaintext", "Plaintext", "plaintext_b64")
	mk, err := DecodeKeyMaterial(ptB64)
	if err != nil {
		return nil, fmt.Errorf("cloud_kms decrypt missing plaintext: %w", err)
	}
	return deriveSMKAndZeroMK(mk, week, clusterSalt, p.Name(), firstString(out, "KeyId", "key_version"))
}

func (p *CloudKMSProvider) Health(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, p.cfg.Endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return err
	}
	_ = resp.Body.Close()
	if resp.StatusCode >= 500 {
		return fmt.Errorf("cloud_kms health HTTP %d", resp.StatusCode)
	}
	return nil
}

func firstString(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := m[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

// CommandProvider executes an external CLI/HSM/SOPS/secret-manager command without shell expansion.
type CommandProvider struct {
	cfg Config
}

// NewCommandProvider validates external command vault bridge config.
func NewCommandProvider(cfg Config) (*CommandProvider, error) {
	if strings.TrimSpace(cfg.CommandBin) == "" {
		return nil, errors.New("command vault provider requires CommandBin")
	}
	return &CommandProvider{cfg: cfg}, nil
}

func (p *CommandProvider) Name() string {
	return "command"
}

func (p *CommandProvider) FetchWeeklySMK(ctx context.Context, week uint64, clusterSalt string) (*SMKResult, error) {
	timeout := p.cfg.Timeout
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(cctx, p.cfg.CommandBin, p.cfg.CommandArgs...)
	cmd.Env = append(os.Environ(),
		fmt.Sprintf("GRID_WEEK=%d", week),
		"GRID_CLUSTER_SALT="+clusterSalt,
		"GRID_PURPOSE=weekly-smk",
	)
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("vault command failed: %w", err)
	}
	var parsed struct {
		SMKB64       string `json:"smk_b64"`
		MasterKeyB64 string `json:"master_key_b64"`
		MasterKey    string `json:"master_key"`
		KeyVersion   string `json:"key_version"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(out), &parsed); err != nil {
		return nil, fmt.Errorf("parse command vault JSON output: %w", err)
	}
	if parsed.SMKB64 != "" {
		smk, err := DecodeKeyMaterial(parsed.SMKB64)
		if err != nil || len(smk) != crypto.KeySize {
			return nil, errors.New("command vault returned invalid smk_b64")
		}
		return &SMKResult{
			Week:       week,
			SMK:        smk,
			Provider:   p.Name(),
			KeyVersion: parsed.KeyVersion,
			FetchedAt:  time.Now().UTC(),
		}, nil
	}
	rawMK := parsed.MasterKeyB64
	if rawMK == "" {
		rawMK = parsed.MasterKey
	}
	mk, err := DecodeKeyMaterial(rawMK)
	if err != nil {
		return nil, errors.New("command vault returned neither smk_b64 nor valid master_key")
	}
	return deriveSMKAndZeroMK(mk, week, clusterSalt, p.Name(), parsed.KeyVersion)
}

func (p *CommandProvider) Health(ctx context.Context) error {
	if _, err := exec.LookPath(p.cfg.CommandBin); err != nil {
		return fmt.Errorf("vault command binary missing: %w", err)
	}
	return nil
}

// DevMemoryProvider is strictly an explicit dev/test-only key seal.
type DevMemoryProvider struct {
	mk []byte
}

// NewDevMemoryProvider rejects usage unless AllowDevVault is explicitly enabled.
func NewDevMemoryProvider(cfg Config) (*DevMemoryProvider, error) {
	if !cfg.AllowDevVault {
		return nil, errors.New("dev_memory vault refused without CRYPTD_ALLOW_DEV_VAULT=true")
	}
	if strings.TrimSpace(cfg.DevMasterKey) == "" {
		return nil, errors.New("dev_memory vault requires DevMasterKey")
	}
	mk, err := DecodeKeyMaterial(cfg.DevMasterKey)
	if err != nil {
		return nil, err
	}
	return &DevMemoryProvider{mk: mk}, nil
}

func (p *DevMemoryProvider) Name() string {
	return "dev_memory"
}

func (p *DevMemoryProvider) FetchWeeklySMK(_ context.Context, week uint64, clusterSalt string) (*SMKResult, error) {
	smk, err := crypto.DeriveWeeklySMK(p.mk, week, clusterSalt)
	if err != nil {
		return nil, err
	}
	return &SMKResult{
		Week:       week,
		SMK:        smk,
		Provider:   p.Name(),
		KeyVersion: "dev-local",
		FetchedAt:  time.Now().UTC(),
	}, nil
}

func (p *DevMemoryProvider) Health(_ context.Context) error {
	if len(p.mk) < 16 {
		return errors.New("dev master key uninitialized")
	}
	return nil
}
