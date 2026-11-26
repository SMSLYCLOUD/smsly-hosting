package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"flag"
	"log"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/dbproxy"
	"smsly-hosting/cryptd/internal/epoch"
	"smsly-hosting/cryptd/internal/httpapi"
	"smsly-hosting/cryptd/internal/vault"
	"smsly-hosting/cryptd/internal/volumes"
)

func envOrDefault(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

func envBool(key string, def bool) bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	switch v {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return def
	}
}

func envDuration(key string, def time.Duration) time.Duration {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	if d, err := time.ParseDuration(v); err == nil && d > 0 {
		return d
	}
	return def
}

func envInt(key string, def int) int {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	if n, err := strconv.Atoi(v); err == nil {
		return n
	}
	return def
}

func main() {
	mode := flag.String("mode", envOrDefault("CRYPTD_MODE", "controller"), "Run mode: 'controller' (PaaS daemon) or 'root-vault' (external master-key server)")
	flag.Parse()

	strictMlock := envBool("CRYPTD_REQUIRE_MLOCK", false)
	for _, w := range crypto.HardenProcessMemory(strictMlock) {
		log.Printf("WARN [cryptd]: %s", w)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	switch strings.ToLower(strings.TrimSpace(*mode)) {
	case "root-vault":
		runRootVault(ctx)
	case "controller":
		runController(ctx)
	default:
		log.Fatalf("FATAL [cryptd]: unknown mode %q (expected 'controller' or 'root-vault')", *mode)
	}
}

func runRootVault(ctx context.Context) {
	rawMK := envOrDefault("GRID_MASTER_KEY", "")
	mk, err := vault.DecodeKeyMaterial(rawMK)
	if err != nil {
		log.Fatalf("FATAL [root-vault]: invalid GRID_MASTER_KEY: %v", err)
	}
	defer crypto.ZeroBytes(mk)

	allowedNodes := make(map[string]bool)
	for _, n := range strings.Split(envOrDefault("ROOT_VAULT_ALLOWED_NODES", ""), ",") {
		n = strings.TrimSpace(n)
		if n != "" {
			allowedNodes[n] = true
		}
	}

	certFile := envOrDefault("ROOT_VAULT_TLS_CERT", "")
	keyFile := envOrDefault("ROOT_VAULT_TLS_KEY", "")
	caFile := envOrDefault("ROOT_VAULT_CLIENT_CA", "")
	if caFile != "" && (certFile == "" || keyFile == "") {
		log.Fatalf("FATAL [root-vault]: ROOT_VAULT_CLIENT_CA requires ROOT_VAULT_TLS_CERT and ROOT_VAULT_TLS_KEY")
	}

	allowPastWeeks := envInt("ROOT_VAULT_ALLOW_PAST_WEEKS", 2)
	if allowPastWeeks < 0 {
		allowPastWeeks = 0
	}
	epochDuration := envDuration("ROOT_VAULT_EPOCH_DURATION", envDuration("CRYPTD_EPOCH_DURATION", 168*time.Hour))

	handler, err := vault.NewRootVaultHandler(vault.RootVaultConfig{
		MasterKey:      mk,
		KeyVersion:     envOrDefault("ROOT_VAULT_KEY_VERSION", "mk-v1"),
		HMACSecret:     envOrDefault("ROOT_VAULT_HMAC_SECRET", ""),
		AuthToken:      envOrDefault("ROOT_VAULT_AUTH_TOKEN", ""),
		ClientCA:       caFile,
		AllowedNodes:   allowedNodes,
		AllowPastWeeks: uint64(allowPastWeeks),
		PinnedSalt:     envOrDefault("ROOT_VAULT_CLUSTER_SALT", ""),
		StateFile:      envOrDefault("ROOT_VAULT_STATE_FILE", ""),
		EpochDuration:  epochDuration,
	})
	crypto.ZeroBytes(mk)
	if err != nil {
		log.Fatalf("FATAL [root-vault]: init failed: %v", err)
	}

	listenAddr := envOrDefault("ROOT_VAULT_LISTEN_ADDR", "0.0.0.0:9443")
	srv := &http.Server{
		Addr:              listenAddr,
		Handler:           handler,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	if caFile != "" {
		caPEM, err := os.ReadFile(caFile)
		if err != nil {
			log.Fatalf("FATAL [root-vault]: read ROOT_VAULT_CLIENT_CA: %v", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caPEM) {
			log.Fatalf("FATAL [root-vault]: invalid client CA PEM")
		}
		srv.TLSConfig = &tls.Config{
			ClientAuth: tls.RequireAndVerifyClientCert,
			ClientCAs:  pool,
			MinVersion: tls.VersionTLS12,
		}
	}

	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(sctx)
	}()

	log.Printf("INFO [root-vault]: listening on %s (mTLS client verification=%v)", listenAddr, caFile != "")
	if certFile != "" && keyFile != "" {
		err = srv.ListenAndServeTLS(certFile, keyFile)
	} else {
		log.Printf("WARN [root-vault]: running without TLS; only use behind WireGuard/VPN/tunnel")
		err = srv.ListenAndServe()
	}
	if err != nil && err != http.ErrServerClosed {
		log.Fatalf("FATAL [root-vault]: %v", err)
	}
}

func runController(ctx context.Context) {
	sharedSecret := envOrDefault("CRYPTD_SHARED_SECRET", "")
	if sharedSecret == "" {
		log.Fatalf("FATAL [cryptd]: CRYPTD_SHARED_SECRET must be set")
	}

	var cmdArgs []string
	if rawArgs := envOrDefault("CRYPTD_VAULT_CMD_ARGS", ""); rawArgs != "" {
		cmdArgs = strings.Fields(rawArgs)
	}

	vaultCfg := vault.Config{
		ProviderName:          envOrDefault("CRYPTD_VAULT_PROVIDER", ""),
		Endpoint:              envOrDefault("CRYPTD_VAULT_ENDPOINT", ""),
		NodeID:                envOrDefault("CRYPTD_NODE_ID", "grid-paas-master"),
		ClusterSalt:           envOrDefault("CRYPTD_CLUSTER_SALT", "trulay-grid-default"),
		Timeout:               envDuration("CRYPTD_VAULT_TIMEOUT", 15*time.Second),
		CAFile:                envOrDefault("CRYPTD_VAULT_CA_FILE", ""),
		ClientCertFile:        envOrDefault("CRYPTD_VAULT_CLIENT_CERT", ""),
		ClientKeyFile:         envOrDefault("CRYPTD_VAULT_CLIENT_KEY", ""),
		InsecureSkipVerify:    envBool("CRYPTD_VAULT_INSECURE_SKIP_VERIFY", false),
		HMACSecret:            envOrDefault("CRYPTD_VAULT_HMAC_SECRET", ""),
		AuthToken:             envOrDefault("CRYPTD_VAULT_TOKEN", ""),
		VaultNamespace:        envOrDefault("CRYPTD_VAULT_NAMESPACE", ""),
		VaultMount:            envOrDefault("CRYPTD_VAULT_MOUNT", ""),
		VaultKVPath:           envOrDefault("CRYPTD_VAULT_KV_PATH", ""),
		VaultKVField:          envOrDefault("CRYPTD_VAULT_KV_FIELD", "master_key"),
		VaultTransitKey:       envOrDefault("CRYPTD_VAULT_TRANSIT_KEY", ""),
		VaultRoleID:           envOrDefault("CRYPTD_VAULT_ROLE_ID", ""),
		VaultSecretID:         envOrDefault("CRYPTD_VAULT_SECRET_ID", ""),
		InfisicalToken:        envOrDefault("CRYPTD_INFISICAL_TOKEN", ""),
		InfisicalClientID:     envOrDefault("CRYPTD_INFISICAL_CLIENT_ID", ""),
		InfisicalClientSecret: envOrDefault("CRYPTD_INFISICAL_CLIENT_SECRET", ""),
		InfisicalWorkspaceID:  envOrDefault("CRYPTD_INFISICAL_WORKSPACE_ID", ""),
		InfisicalEnvironment:  envOrDefault("CRYPTD_INFISICAL_ENVIRONMENT", "prod"),
		InfisicalSecretName:   envOrDefault("CRYPTD_INFISICAL_SECRET_NAME", "GRID_MASTER_KEY"),
		InfisicalSecretPath:   envOrDefault("CRYPTD_INFISICAL_SECRET_PATH", "/"),
		KMSMode:               envOrDefault("CRYPTD_KMS_MODE", "hmac"),
		KMSKeyID:              envOrDefault("CRYPTD_KMS_KEY_ID", ""),
		KMSCiphertextB64:      envOrDefault("CRYPTD_KMS_CIPHERTEXT_B64", ""),
		KMSAuthHeaderName:     envOrDefault("CRYPTD_KMS_AUTH_HEADER", "Authorization"),
		KMSAuthCmd:            envOrDefault("CRYPTD_KMS_AUTH_CMD", ""),
		CommandBin:            envOrDefault("CRYPTD_VAULT_CMD", ""),
		CommandArgs:           cmdArgs,
		AllowDevVault:         envBool("CRYPTD_ALLOW_DEV_VAULT", false),
		DevMasterKey:          envOrDefault("CRYPTD_DEV_MASTER_KEY", ""),
	}

	provider, err := vault.NewProvider(vaultCfg)
	if err != nil {
		log.Fatalf("FATAL [cryptd]: vault provider init: %v", err)
	}

	keystoreDir := envOrDefault("CRYPTD_KEYSTORE_DIR", "/var/lib/smsly/keystore")
	store, err := crypto.OpenKeyStore(keystoreDir)
	if err != nil {
		log.Fatalf("FATAL [cryptd]: keystore open: %v", err)
	}

	epochCfg := epoch.DefaultConfig()
	epochCfg.EpochDuration = envDuration("CRYPTD_EPOCH_DURATION", 168*time.Hour)
	epochCfg.GraceDuration = envDuration("CRYPTD_GRACE_DURATION", 24*time.Hour)
	epochCfg.PreRotateLead = envDuration("CRYPTD_PREROTATE_LEAD", 24*time.Hour)
	epochCfg.PollInterval = envDuration("CRYPTD_POLL_INTERVAL", time.Minute)
	epochCfg.ClusterSalt = vaultCfg.ClusterSalt
	epochCfg.FailClosedOnBoot = envBool("CRYPTD_FAIL_CLOSED_BOOT", true)
	epochCfg.DefaultLeaseTTL = envDuration("CRYPTD_DEFAULT_LEASE_TTL", 2*time.Hour)
	epochCfg.DefaultOptedIn = false

	epochMgr, err := epoch.NewManager(epochCfg, provider, store)
	if err != nil {
		log.Fatalf("FATAL [cryptd]: epoch manager init: %v", err)
	}

	defaultPaaSPass := dbproxy.ServiceTarget{
		ServiceID:        crypto.ControlPlaneServiceID,
		ProjectID:        "control-plane",
		UpstreamPG:       envOrDefault("CRYPTD_PG_UPSTREAM", ""),
		UpstreamRedis:    envOrDefault("CRYPTD_REDIS_UPSTREAM", ""),
		OptedIn:          epochCfg.DefaultOptedIn,
		EncryptRedisVals: epochCfg.DefaultOptedIn && envBool("CRYPTD_REDIS_ENCRYPT_VALUES", true),
	}
	router := dbproxy.NewRouter(defaultPaaSPass)

	// Re-hydrate persisted service DB/Redis routing tables into router on restart.
	if recs, err := store.ListServices(); err == nil {
		for _, rec := range recs {
			encVals := rec.OptedIn && rec.EncryptRedisVals && envBool("CRYPTD_REDIS_ENCRYPT_VALUES", true)
			router.Register(dbproxy.ServiceTarget{
				ServiceID:        rec.ServiceID,
				ProjectID:        rec.ProjectID,
				DBRoles:          rec.DBRoles,
				DBNames:          rec.DBNames,
				RedisUsers:       rec.RedisUsers,
				RedisPrefixes:    rec.RedisPrefixes,
				UpstreamPG:       rec.UpstreamPG,
				UpstreamRedis:    rec.UpstreamRedis,
				UpstreamTLS:      rec.UpstreamRedisTLS,
				OptedIn:          rec.OptedIn,
				EncryptRedisVals: encVals,
			})
		}
	}

	volMgr, err := volumes.NewManager(volumes.Config{
		BaseDir:    envOrDefault("SMSLY_ENCRYPTED_VOLUME_DIR", "/var/lib/smsly/encrypted-volumes"),
		ScriptPath: envOrDefault("SMSLY_VOLUME_CRYPT_SCRIPT", "/opt/smsly-hosting/scripts/smsly-volume-crypt.sh"),
		Backend:    envOrDefault("SMSLY_VOLUME_CRYPTO_BACKEND", "auto"),
		AllowMock:  envBool("CRYPTD_ALLOW_MOCK_VOLUMES", false),
		CmdTimeout: envDuration("CRYPTD_VOLUME_CMD_TIMEOUT", 120*time.Second),
	}, epochMgr, store, nil)
	if err != nil {
		log.Fatalf("FATAL [cryptd]: volume manager init: %v", err)
	}

	// Point-of-contact lock wiring: on global grace expiry OR per-service lock,
	// sever live DB/Redis sockets AND lock encrypted host volumes immediately!
	epochMgr.SetCallbacks(
		func(reason string) {
			closed := router.CloseAllConns()
			lockedVols := volMgr.LockAll(ctx)
			log.Printf("ERROR [cryptd]: GLOBAL LOCKDOWN (%s) - closed %d live DB/Redis connections, locked %d volumes", reason, closed, lockedVols)
		},
		func(serviceID, reason string) {
			closed := router.CloseServiceConns(serviceID)
			log.Printf("WARN [cryptd]: SERVICE LOCK %s (%s) - closed %d live DB/Redis connections", serviceID, reason, closed)
			go func(svcID, rsn string) {
				if _, allowed, _ := epochMgr.GetServicePolicy(svcID); allowed {
					return
				}
				lctx, cancel := context.WithTimeout(ctx, 120*time.Second)
				defer cancel()
				lockedVols := volMgr.LockServiceVolumes(lctx, svcID)
				log.Printf("WARN [cryptd]: SERVICE LOCK %s (%s) - locked %d volumes", svcID, rsn, lockedVols)
			}(serviceID, reason)
		},
	)
	epochMgr.SetServiceUnlockCallback(func(serviceID string) error {
		return volMgr.UnlockServiceVolumes(ctx, serviceID)
	})

	if err := epochMgr.Start(ctx); err != nil {
		log.Printf("ERROR [cryptd]: boot vault sync failed: %v (state=%s)", err, epochMgr.Status().State)
	} else if recs, lerr := store.ListServices(); lerr == nil {
		for _, rec := range recs {
			if rec == nil || !rec.OptedIn || rec.Locked {
				continue
			}
			if len(rec.VolumeNames) == 0 && len(rec.VolumeDEKs) == 0 {
				continue
			}
			if !rec.LeasePersistent && !rec.LeaseExpiresAt.IsZero() && time.Now().UTC().After(rec.LeaseExpiresAt) {
				continue
			}
			if uerr := volMgr.UnlockServiceVolumes(ctx, rec.ServiceID); uerr != nil {
				log.Printf("WARN [cryptd]: boot volume unlock failed for %s: %v", rec.ServiceID, uerr)
			} else {
				log.Printf("INFO [cryptd]: unlocked encrypted volumes on boot for %s", rec.ServiceID)
			}
		}
	}

	// Optional PostgreSQL Wire Point-of-Contact Gate
	if pgListen := envOrDefault("CRYPTD_PG_LISTEN", ""); pgListen != "" {
		pgUpstream := envOrDefault("CRYPTD_PG_UPSTREAM", "127.0.0.1:6432")
		pgClientTLS := buildPGProxyClientTLS(keystoreDir)
		pgUpstreamTLS, pgUpstreamOpt, pgExplicitCA := buildPGProxyUpstreamTLS()
		pgProxy, err := dbproxy.NewPGProxy(dbproxy.PGProxyConfig{
			ListenAddr:          pgListen,
			DefaultUpstream:     pgUpstream,
			DialTimeout:         10 * time.Second,
			ClientTLS:           pgClientTLS,
			UpstreamTLS:         pgUpstreamTLS,
			UpstreamTLSOptional: pgUpstreamOpt,
			ExplicitCA:          pgExplicitCA,
		}, epochMgr, router)
		if err != nil {
			log.Fatalf("FATAL [cryptd]: pgproxy init: %v", err)
		}
		go func() {
			log.Printf("INFO [cryptd]: Postgres wire proxy listening on %s -> %s (client_tls=%v, upstream_tls=%v)",
				pgListen, pgUpstream, pgClientTLS != nil, pgUpstreamTLS != nil)
			if err := pgProxy.ListenAndServe(ctx); err != nil {
				log.Printf("ERROR [cryptd]: pgproxy stopped: %v", err)
			}
		}()
	}

	// Optional Redis RESP Point-of-Contact Gate & Transparent Value Encryptor
	if redisListen := envOrDefault("CRYPTD_REDIS_LISTEN", ""); redisListen != "" {
		redisUpstream := envOrDefault("CRYPTD_REDIS_UPSTREAM", "127.0.0.1:6379")
		redisUpstreamTLS, redisUpstreamOpt, redisExplicitCA := buildRedisProxyUpstreamTLS()
		redisProxy, err := dbproxy.NewRedisProxy(dbproxy.RedisProxyConfig{
			ListenAddr:          redisListen,
			DefaultUpstream:     redisUpstream,
			DefaultServiceID:    envOrDefault("CRYPTD_REDIS_DEFAULT_SERVICE", crypto.ControlPlaneServiceID),
			DialTimeout:         10 * time.Second,
			TransparentEncrypt:  envBool("CRYPTD_REDIS_ENCRYPT_VALUES", true),
			UpstreamTLS:         redisUpstreamTLS,
			UpstreamTLSOptional: redisUpstreamOpt,
			ExplicitCA:          redisExplicitCA,
		}, epochMgr, router)
		if err != nil {
			log.Fatalf("FATAL [cryptd]: redisproxy init: %v", err)
		}
		go func() {
			log.Printf("INFO [cryptd]: Redis RESP proxy listening on %s -> %s (encrypt=%v)",
				redisListen, redisUpstream, envBool("CRYPTD_REDIS_ENCRYPT_VALUES", true))
			if err := redisProxy.ListenAndServe(ctx); err != nil {
				log.Printf("ERROR [cryptd]: redisproxy stopped: %v", err)
			}
		}()
	}

	httpHandler, err := httpapi.NewServer(sharedSecret, epochMgr, volMgr, router)
	if err != nil {
		log.Fatalf("FATAL [cryptd]: httpapi init: %v", err)
	}

	listenAddr := envOrDefault("CRYPTD_LISTEN_ADDR", "127.0.0.1:9105")
	httpSrv := &http.Server{
		Addr:              listenAddr,
		Handler:           httpHandler,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      150 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
	}()

	log.Printf("INFO [cryptd]: controller HTTP API listening on %s (vault=%s, state=%s, opt_in_default=%v)",
		listenAddr, provider.Name(), epochMgr.Status().State, epochCfg.DefaultOptedIn)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("FATAL [cryptd]: http server error: %v", err)
	}
}

func buildPGProxyClientTLS(keystoreDir string) *tls.Config {
	certFile := envOrDefault("CRYPTD_PG_TLS_CERT", "")
	keyFile := envOrDefault("CRYPTD_PG_TLS_KEY", "")
	if certFile != "" && keyFile != "" {
		cert, err := tls.LoadX509KeyPair(certFile, keyFile)
		if err != nil {
			log.Printf("WARN [cryptd]: failed loading CRYPTD_PG_TLS_CERT/KEY: %v", err)
		} else {
			return &tls.Config{
				Certificates: []tls.Certificate{cert},
				MinVersion:   tls.VersionTLS12,
			}
		}
	}

	mode := strings.ToLower(envOrDefault("CRYPTD_PG_CLIENT_TLS", "auto"))
	if mode == "false" || mode == "0" || mode == "off" || mode == "no" {
		return nil
	}

	autoCert := filepath.Join(keystoreDir, "pgproxy-tls.crt")
	autoKey := filepath.Join(keystoreDir, "pgproxy-tls.key")
	if cert, err := tls.LoadX509KeyPair(autoCert, autoKey); err == nil {
		return &tls.Config{
			Certificates: []tls.Certificate{cert},
			MinVersion:   tls.VersionTLS12,
		}
	}

	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		log.Printf("WARN [cryptd]: failed generating pgproxy TLS key: %v", err)
		return nil
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 62))
	tmpl := x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "grid-cryptd-pgproxy"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(3650 * 24 * time.Hour),
		KeyUsage:     x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     []string{"host.docker.internal", "localhost", "grid-cryptd"},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("10.100.0.1"), net.ParseIP("172.17.0.1")},
	}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &priv.PublicKey, priv)
	if err != nil {
		log.Printf("WARN [cryptd]: failed creating pgproxy TLS cert: %v", err)
		return nil
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyBytes, err := x509.MarshalECPrivateKey(priv)
	if err != nil {
		return nil
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})
	_ = os.WriteFile(autoCert, certPEM, 0o600)
	_ = os.WriteFile(autoKey, keyPEM, 0o600)
	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		return nil
	}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		MinVersion:   tls.VersionTLS12,
	}
}

var defaultAddonCAFiles = []string{
	"/var/lib/smsly/addon-ca/ca.crt",
	"/opt/smsly-hosting/builds/addon-ca/ca.crt",
}

func loadUpstreamRootCAs(explicitCAFile string) *x509.CertPool {
	var pool *x509.CertPool
	if sys, serr := x509.SystemCertPool(); serr == nil && sys != nil {
		pool = sys
	} else {
		pool = x509.NewCertPool()
	}
	if explicitCAFile != "" {
		if caPEM, err := os.ReadFile(explicitCAFile); err == nil {
			pool.AppendCertsFromPEM(caPEM)
		}
	}
	for _, caPath := range defaultAddonCAFiles {
		if caPEM, err := os.ReadFile(caPath); err == nil {
			pool.AppendCertsFromPEM(caPEM)
		}
	}
	return pool
}

func buildPGProxyUpstreamTLS() (*tls.Config, bool, bool) {
	mode := strings.ToLower(envOrDefault("CRYPTD_PG_UPSTREAM_TLS", "auto"))
	if mode == "false" || mode == "0" || mode == "off" || mode == "no" {
		return nil, false, false
	}
	caFile := envOrDefault("CRYPTD_PG_UPSTREAM_CA", "")
	optional := (mode == "auto")
	cfg := &tls.Config{
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: envBool("CRYPTD_PG_TLS_INSECURE_SKIP_VERIFY", false),
		RootCAs:            loadUpstreamRootCAs(caFile),
	}
	return cfg, optional, caFile != ""
}

func buildRedisProxyUpstreamTLS() (*tls.Config, bool, bool) {
	mode := strings.ToLower(envOrDefault("CRYPTD_REDIS_UPSTREAM_TLS", "auto"))
	if mode == "false" || mode == "0" || mode == "off" || mode == "no" {
		return nil, false, false
	}
	caFile := envOrDefault("CRYPTD_REDIS_UPSTREAM_CA", "")
	optional := (mode == "auto")
	cfg := &tls.Config{
		MinVersion:         tls.VersionTLS12,
		RootCAs:            loadUpstreamRootCAs(caFile),
		InsecureSkipVerify: envBool("CRYPTD_REDIS_TLS_INSECURE_SKIP_VERIFY", false),
	}
	return cfg, optional, caFile != ""
}
