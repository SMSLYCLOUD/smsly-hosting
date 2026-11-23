package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
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

	handler, err := vault.NewRootVaultHandler(vault.RootVaultConfig{
		MasterKey:      mk,
		KeyVersion:     envOrDefault("ROOT_VAULT_KEY_VERSION", "mk-v1"),
		HMACSecret:     envOrDefault("ROOT_VAULT_HMAC_SECRET", ""),
		AuthToken:      envOrDefault("ROOT_VAULT_AUTH_TOKEN", ""),
		AllowedNodes:   allowedNodes,
		AllowPastWeeks: uint64(envInt("ROOT_VAULT_ALLOW_PAST_WEEKS", 2)),
		PinnedSalt:     envOrDefault("ROOT_VAULT_CLUSTER_SALT", ""),
		StateFile:      envOrDefault("ROOT_VAULT_STATE_FILE", ""),
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
	}

	certFile := envOrDefault("ROOT_VAULT_TLS_CERT", "")
	keyFile := envOrDefault("ROOT_VAULT_TLS_KEY", "")
	caFile := envOrDefault("ROOT_VAULT_CLIENT_CA", "")
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
	sharedSecret := envOrDefault("CRYPTD_SHARED_SECRET", envOrDefault("NAPD_SHARED_SECRET", ""))
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
			lockedVols := volMgr.LockServiceVolumes(ctx, serviceID)
			log.Printf("WARN [cryptd]: SERVICE LOCK %s (%s) - closed %d live DB/Redis connections, locked %d volumes", serviceID, reason, closed, lockedVols)
		},
	)
	epochMgr.SetServiceUnlockCallback(func(serviceID string) error {
		return volMgr.UnlockServiceVolumes(ctx, serviceID)
	})

	if err := epochMgr.Start(ctx); err != nil {
		log.Printf("ERROR [cryptd]: boot vault sync failed: %v (state=%s)", err, epochMgr.Status().State)
	}

	// Optional PostgreSQL Wire Point-of-Contact Gate
	if pgListen := envOrDefault("CRYPTD_PG_LISTEN", ""); pgListen != "" {
		pgUpstream := envOrDefault("CRYPTD_PG_UPSTREAM", "127.0.0.1:6432")
		pgProxy, err := dbproxy.NewPGProxy(dbproxy.PGProxyConfig{
			ListenAddr:      pgListen,
			DefaultUpstream: pgUpstream,
			DialTimeout:     10 * time.Second,
		}, epochMgr, router)
		if err != nil {
			log.Fatalf("FATAL [cryptd]: pgproxy init: %v", err)
		}
		go func() {
			log.Printf("INFO [cryptd]: Postgres wire proxy listening on %s -> %s", pgListen, pgUpstream)
			if err := pgProxy.ListenAndServe(ctx); err != nil {
				log.Printf("ERROR [cryptd]: pgproxy stopped: %v", err)
			}
		}()
	}

	// Optional Redis RESP Point-of-Contact Gate & Transparent Value Encryptor
	if redisListen := envOrDefault("CRYPTD_REDIS_LISTEN", ""); redisListen != "" {
		redisUpstream := envOrDefault("CRYPTD_REDIS_UPSTREAM", "127.0.0.1:6379")
		redisProxy, err := dbproxy.NewRedisProxy(dbproxy.RedisProxyConfig{
			ListenAddr:         redisListen,
			DefaultUpstream:    redisUpstream,
			DefaultServiceID:   envOrDefault("CRYPTD_REDIS_DEFAULT_SERVICE", crypto.ControlPlaneServiceID),
			DialTimeout:        10 * time.Second,
			TransparentEncrypt: envBool("CRYPTD_REDIS_ENCRYPT_VALUES", true),
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
	}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
		_ = fmt.Sprintf("shutdown complete")
	}()

	log.Printf("INFO [cryptd]: controller HTTP API listening on %s (vault=%s, state=%s, opt_in_default=%v)",
		listenAddr, provider.Name(), epochMgr.Status().State, epochCfg.DefaultOptedIn)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("FATAL [cryptd]: http server error: %v", err)
	}
}
