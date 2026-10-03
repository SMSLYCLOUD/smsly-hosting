// Command napd is the SMSLY infra-tier sleep/wake daemon.
//
// Sablier sleeps tenant workloads; napd does the same for PaaS infra
// tiers (build caches, dashboards, secrets manager) defined in tiers.d:
//
//	GET  /health          — liveness, unauthenticated ("ok").
//	GET  /status          — JSON tier states (needs X-Napd-Secret).
//	GET|POST /wake?tier=X[&timeout=120]  — start tier, wait until its
//	                           containers run. Needs X-Napd-Secret.
//	GET|POST /sleep?tier=X[&force=1]      — stop tier. Refuses tiers
//	                           without SMSLY_TIER_AUTOSLEEP=1 unless
//	                           force=1. Needs X-Napd-Secret.
//
// Plus a reaper loop: tiers with SMSLY_TIER_AUTOSLEEP=1 and
// SMSLY_TIER_IDLE_SECS>0 are stopped once awake longer than the idle
// window. Duration-based, NOT activity-based — only tiers safe to
// interrupt may opt in (caches, dashboards). Request-path, security,
// alerting and HA tiers must never set AUTOSLEEP (see
// infrastructure/systemd/README.md).
//
// Design rules:
//   - stdlib only (net/http, os/exec, encoding/json, ...). No deps.
//   - never shells to systemctl (no D-Bus dependency): all lifecycle
//     goes through scripts/smsly-tier.sh up/down, which owns the
//     .asleep markers napd also reads.
//   - Docker state via the `docker` CLI (no SDK): match containers by
//     the com.docker.compose.service label, not by name prefix.
//   - fail-closed: no configured secret => wake/sleep/status all 503.
//     Wrong secret => 403. Bad tier name => 400, never exec'd.
package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

var tierNameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,31}$`)

type tierConf struct {
	name      string
	services  []string
	autosleep bool
	idleSecs  int
}

type config struct {
	listen     string
	tierDir    string
	stateDir   string
	wakeDir    string
	installDir string
	tierScript string
	secretFile string
	secret     string
	reapSecs   int
}

type daemon struct {
	cfg      config
	mu       sync.Mutex // guards lastWake + state file writes
	lastWake map[string]time.Time
	perTier  map[string]*sync.Mutex
	tierMu   sync.Mutex // guards perTier map itself
}

func getenv(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

// parseTierConf reads a tiers.d/*.conf shell fragment for the keys napd
// needs. Values may be quoted or bare; anything else is ignored.
func parseTierConf(name, path string) (*tierConf, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	tc := &tierConf{name: name}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, val, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		val = strings.Trim(strings.TrimSpace(val), `"'`)
		switch key {
		case "SMSLY_TIER_SERVICES":
			tc.services = strings.Fields(val)
		case "SMSLY_TIER_AUTOSLEEP":
			tc.autosleep = val == "1"
		case "SMSLY_TIER_IDLE_SECS":
			if n, err := strconv.Atoi(val); err == nil && n > 0 {
				tc.idleSecs = n
			}
		}
	}
	if len(tc.services) == 0 {
		return nil, fmt.Errorf("tier %q defines no SMSLY_TIER_SERVICES", name)
	}
	return tc, nil
}

func (d *daemon) tier(name string) (*tierConf, error) {
	if !tierNameRE.MatchString(name) {
		return nil, fmt.Errorf("bad tier name")
	}
	return parseTierConf(name, filepath.Join(d.cfg.tierDir, name+".conf"))
}

func (d *daemon) tierLock(name string) *sync.Mutex {
	d.tierMu.Lock()
	defer d.tierMu.Unlock()
	if d.perTier == nil {
		d.perTier = map[string]*sync.Mutex{}
	}
	if m, ok := d.perTier[name]; ok {
		return m
	}
	m := &sync.Mutex{}
	d.perTier[name] = m
	return m
}

// scriptEnv mirrors the systemd unit environment so smsly-tier.sh uses
// the same dirs whether napd or systemd invoked it.
func (d *daemon) scriptEnv() []string {
	return []string{
		"SMSLY_INSTALL_DIR=" + d.cfg.installDir,
		"SMSLY_TIER_DIR=" + d.cfg.tierDir,
		"SMSLY_TIER_STATE_DIR=" + d.cfg.stateDir,
		"SMSLY_TIER_WAKE_DIR=" + d.cfg.wakeDir,
		"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
	}
}

func (d *daemon) runTier(ctx context.Context, verb, tier string) (string, error) {
	cmd := exec.CommandContext(ctx, d.cfg.tierScript, verb, tier)
	cmd.Env = d.scriptEnv()
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// runningServices returns the set of compose service names with at least
// one running container, matched by label (immune to name prefixes).
func runningServices(ctx context.Context) map[string]bool {
	out := map[string]bool{}
	cmd := exec.CommandContext(ctx, "docker", "ps", "--format", `{{.Label "com.docker.compose.service"}}`)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	if err := cmd.Run(); err != nil {
		return out
	}
	for _, line := range strings.Split(buf.String(), "\n") {
		if s := strings.TrimSpace(line); s != "" && s != "<no value>" {
			out[s] = true
		}
	}
	return out
}

func (d *daemon) awake(ctx context.Context, tc *tierConf) bool {
	running := runningServices(ctx)
	for _, svc := range tc.services {
		if running[svc] {
			return true
		}
	}
	return false
}

func (d *daemon) markWake(tier string) {
	statePath := filepath.Join(d.cfg.stateDir, "napd-state.json")
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.lastWake == nil {
		d.lastWake = map[string]time.Time{}
	}
	d.lastWake[tier] = time.Now()
	raw, _ := json.Marshal(d.lastWake)
	tmp := statePath + ".tmp"
	if os.WriteFile(tmp, raw, 0o644) == nil {
		_ = os.Rename(tmp, statePath)
	}
}

func (d *daemon) loadState() {
	raw, err := os.ReadFile(filepath.Join(d.cfg.stateDir, "napd-state.json"))
	if err != nil {
		return
	}
	var m map[string]time.Time
	if json.Unmarshal(raw, &m) == nil {
		d.lastWake = m
	}
}

func (d *daemon) checkSecret(r *http.Request) bool {
	if len(d.cfg.secret) == 0 {
		return false
	}
	got := strings.TrimSpace(r.Header.Get("X-Napd-Secret"))
	if got == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(got), []byte(d.cfg.secret)) == 1
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func (d *daemon) healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func (d *daemon) statusHandler(w http.ResponseWriter, r *http.Request) {
	if !d.checkSecret(r) {
		if len(d.cfg.secret) == 0 {
			http.Error(w, "napd secret not configured", http.StatusServiceUnavailable)
			return
		}
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	entries, _ := os.ReadDir(d.cfg.tierDir)
	running := runningServices(ctx)
	out := map[string]any{}
	for _, e := range entries {
		name := strings.TrimSuffix(e.Name(), ".conf")
		if !strings.HasSuffix(e.Name(), ".conf") || !tierNameRE.MatchString(name) {
			continue
		}
		tc, err := parseTierConf(name, filepath.Join(d.cfg.tierDir, e.Name()))
		if err != nil {
			continue
		}
		awake := false
		for _, svc := range tc.services {
			if running[svc] {
				awake = true
				break
			}
		}
		d.mu.Lock()
		lw := d.lastWake[name]
		d.mu.Unlock()
		out[name] = map[string]any{
			"awake":      awake,
			"services":   tc.services,
			"autosleep":  tc.autosleep,
			"idle_secs":  tc.idleSecs,
			"last_wake":  lw.Format(time.RFC3339),
			"asleep_mark": func() bool { _, err := os.Stat(filepath.Join(d.cfg.stateDir, name+".asleep")); return err == nil }(),
		}
	}
	writeJSON(w, http.StatusOK, out)
}

func (d *daemon) wakeHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !d.checkSecret(r) {
		if len(d.cfg.secret) == 0 {
			http.Error(w, "napd secret not configured", http.StatusServiceUnavailable)
			return
		}
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	tier := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("tier")))
	tc, err := d.tier(tier)
	if err != nil {
		http.Error(w, "unknown tier", http.StatusNotFound)
		return
	}
	timeoutSecs := 120
	if n, err := strconv.Atoi(r.URL.Query().Get("timeout")); err == nil {
		timeoutSecs = max(10, min(n, 600))
	}
	lock := d.tierLock(tier)
	lock.Lock()
	defer lock.Unlock()

	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(timeoutSecs)*time.Second)
	defer cancel()
	if out, err := d.runTier(ctx, "up", tier); err != nil {
		log.Printf("wake %s: tier up failed: %v (%s)", tier, err, out)
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "tier": tier, "error": "tier up failed"})
		return
	}
	d.markWake(tier)
	deadline := time.Now().Add(time.Duration(timeoutSecs) * time.Second)
	for {
		if d.awake(ctx, tc) {
			writeJSON(w, http.StatusOK, map[string]any{"ok": true, "tier": tier})
			return
		}
		if time.Now().After(deadline) || ctx.Err() != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "tier": tier, "error": "timeout waiting for containers"})
			return
		}
		select {
		case <-ctx.Done():
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "tier": tier, "error": "cancelled"})
			return
		case <-time.After(3 * time.Second):
		}
	}
}

func (d *daemon) sleepHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !d.checkSecret(r) {
		if len(d.cfg.secret) == 0 {
			http.Error(w, "napd secret not configured", http.StatusServiceUnavailable)
			return
		}
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	tier := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("tier")))
	tc, err := d.tier(tier)
	if err != nil {
		http.Error(w, "unknown tier", http.StatusNotFound)
		return
	}
	force := r.URL.Query().Get("force") == "1"
	if !tc.autosleep && !force {
		http.Error(w, "tier is not autosleepable (pass force=1 to override)", http.StatusForbidden)
		return
	}
	lock := d.tierLock(tier)
	lock.Lock()
	defer lock.Unlock()
	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()
	if out, err := d.runTier(ctx, "down", tier); err != nil {
		log.Printf("sleep %s: tier down failed: %v (%s)", tier, err, out)
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "tier": tier, "error": "tier down failed"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "tier": tier})
}

// reap stops AUTOSLEEP tiers awake past their idle window. Duration-based
// only — see the package comment for why this is opt-in per tier.
func (d *daemon) reap() {
	entries, err := os.ReadDir(d.cfg.tierDir)
	if err != nil {
		return
	}
	for _, e := range entries {
		name := strings.TrimSuffix(e.Name(), ".conf")
		if !strings.HasSuffix(e.Name(), ".conf") || !tierNameRE.MatchString(name) {
			continue
		}
		tc, err := parseTierConf(name, filepath.Join(d.cfg.tierDir, e.Name()))
		if err != nil || !tc.autosleep || tc.idleSecs <= 0 {
			continue
		}
		d.mu.Lock()
		lw := d.lastWake[name]
		d.mu.Unlock()
		if lw.IsZero() {
			continue
		}
		if time.Since(lw) < time.Duration(tc.idleSecs)*time.Second {
			continue
		}
		lock := d.tierLock(name)
		if !lock.TryLock() {
			continue
		}
		func() {
			defer lock.Unlock()
			ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
			defer cancel()
			if !d.awake(ctx, tc) {
				return
			}
			if out, err := d.runTier(ctx, "down", name); err != nil {
				log.Printf("reap %s: tier down failed: %v (%s)", name, err, out)
				return
			}
			log.Printf("reap %s: slept after %ds awake", name, tc.idleSecs)
		}()
	}
}

func loadSecret(cfg *config) {
	if cfg.secretFile != "" {
		if raw, err := os.ReadFile(cfg.secretFile); err == nil {
			if v := strings.TrimSpace(string(raw)); v != "" {
				cfg.secret = v
				return
			}
		}
	}
	cfg.secret = strings.TrimSpace(os.Getenv("NAPD_SHARED_SECRET"))
}

func main() {
	var cfg config
	flag.StringVar(&cfg.listen, "listen", getenv("NAPD_LISTEN", "0.0.0.0:8972"), "listen address (0.0.0.0: backend containers arrive via bridge gateway IP)")
	flag.StringVar(&cfg.tierDir, "tiers-dir", getenv("SMSLY_TIER_DIR", "/etc/smsly/tiers.d"), "tier definitions dir")
	flag.StringVar(&cfg.stateDir, "state-dir", getenv("SMSLY_TIER_STATE_DIR", "/var/lib/smsly/tiers"), "tier state dir (shares .asleep markers with smsly-tier.sh)")
	flag.StringVar(&cfg.wakeDir, "wake-dir", getenv("SMSLY_TIER_WAKE_DIR", "/run/smsly/tier-wake"), "wake sentinel dir")
	flag.StringVar(&cfg.installDir, "install-dir", getenv("SMSLY_INSTALL_DIR", "/opt/smsly-hosting"), "repo install dir")
	flag.StringVar(&cfg.tierScript, "tier-script", getenv("SMSLY_TIER_SCRIPT", "/opt/smsly-hosting/scripts/smsly-tier.sh"), "tier lifecycle script")
	flag.StringVar(&cfg.secretFile, "secret-file", getenv("NAPD_SECRET_FILE", ""), "file holding NAPD_SHARED_SECRET")
	flag.IntVar(&cfg.reapSecs, "reap-secs", 60, "reaper interval seconds")
	flag.Parse()
	loadSecret(&cfg)
	if len(cfg.secret) == 0 {
		log.Printf("WARNING: NAPD_SHARED_SECRET unset — wake/sleep/status will 503 until configured")
	}

	d := &daemon{cfg: cfg}
	d.loadState()
	// Tiers already awake at boot get a fresh idle window instead of
	// being reaped on the first tick from a stale pre-restart timestamp.
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		running := runningServices(ctx)
		entries, err := os.ReadDir(cfg.tierDir)
		if err != nil {
			return
		}
		for _, e := range entries {
			name := strings.TrimSuffix(e.Name(), ".conf")
			if !strings.HasSuffix(e.Name(), ".conf") || !tierNameRE.MatchString(name) {
				continue
			}
			tc, err := parseTierConf(name, filepath.Join(cfg.tierDir, e.Name()))
			if err != nil {
				continue
			}
			for _, svc := range tc.services {
				if running[svc] {
					d.mu.Lock()
					if d.lastWake == nil {
						d.lastWake = map[string]time.Time{}
					}
					d.lastWake[name] = time.Now()
					d.mu.Unlock()
					break
				}
			}
		}
	}()

	if cfg.reapSecs < 30 {
		cfg.reapSecs = 60
	}
	go func() {
		t := time.NewTicker(time.Duration(cfg.reapSecs) * time.Second)
		defer t.Stop()
		for range t.C {
			d.reap()
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/health", d.healthHandler)
	mux.HandleFunc("/status", d.statusHandler)
	mux.HandleFunc("/wake", d.wakeHandler)
	mux.HandleFunc("/sleep", d.sleepHandler)

	srv := &http.Server{
		Addr:              cfg.listen,
		Handler:           mux,
		ReadTimeout:       10 * time.Second,
		ReadHeaderTimeout: 5 * time.Second,
		WriteTimeout:      620 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Printf("napd listening on %s (tiers=%s)", cfg.listen, cfg.tierDir)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("listen: %v", err)
	}
}
