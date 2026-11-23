package httpapi

import (
	"bytes"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"smsly-hosting/cryptd/internal/crypto"
	"smsly-hosting/cryptd/internal/dbproxy"
	"smsly-hosting/cryptd/internal/epoch"
	"smsly-hosting/cryptd/internal/volumes"
)

// Server exposes grid-cryptd's HTTP / Unix-socket control, volume, backup, and transit API.
type Server struct {
	sharedSecret string
	epochs       *epoch.Manager
	volumes      *volumes.Manager
	router       *dbproxy.Router
	mux          *http.ServeMux
}

// NewServer constructs an authenticated HTTP handler for grid-cryptd.
func NewServer(sharedSecret string, epochs *epoch.Manager, vols *volumes.Manager, router *dbproxy.Router) (*Server, error) {
	if strings.TrimSpace(sharedSecret) == "" {
		return nil, errors.New("sharedSecret cannot be empty (fail-closed auth)")
	}
	s := &Server{
		sharedSecret: sharedSecret,
		epochs:       epochs,
		volumes:      vols,
		router:       router,
		mux:          http.NewServeMux(),
	}
	s.registerRoutes()
	return s, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *Server) registerRoutes() {
	s.mux.HandleFunc("/health", s.handlePublicHealth)
	s.mux.HandleFunc("/v1/status", s.auth(s.handleStatus))
	s.mux.HandleFunc("/v1/vault/sync", s.auth(s.handleVaultSync))
	s.mux.HandleFunc("/v1/vault/lockdown", s.auth(s.handleVaultLockdown))

	// Volume encryption endpoints (compatible with volume_encryption.py + napd contract)
	s.mux.HandleFunc("/volumes/health", s.auth(s.handleVolumesHealth))
	s.mux.HandleFunc("/volumes/provision", s.auth(s.handleVolumesProvision))
	s.mux.HandleFunc("/volumes/unlock", s.auth(s.handleVolumesUnlock))
	s.mux.HandleFunc("/volumes/lock", s.auth(s.handleVolumesLock))
	s.mux.HandleFunc("/volumes/cleanup", s.auth(s.handleVolumesCleanup))

	// Per-service opt-in registration, leases, and point-of-contact lock/unlock
	s.mux.HandleFunc("/v1/services/register", s.auth(s.handleServiceRegister))
	s.mux.HandleFunc("/v1/services/", s.auth(s.handleServiceSubroute))

	// Field & backup encryption transit APIs
	s.mux.HandleFunc("/v1/transit/encrypt", s.auth(s.handleTransitEncrypt))
	s.mux.HandleFunc("/v1/transit/decrypt", s.auth(s.handleTransitDecrypt))
	s.mux.HandleFunc("/v1/backups/wrap-key", s.auth(s.handleBackupWrapKey))
	s.mux.HandleFunc("/v1/backups/unwrap-key", s.auth(s.handleBackupUnwrapKey))
}

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		supplied := r.Header.Get("X-Cryptd-Secret")
		if supplied == "" {
			supplied = r.Header.Get("X-Napd-Secret")
		}
		if supplied == "" {
			if authHdr := r.Header.Get("Authorization"); strings.HasPrefix(authHdr, "Bearer ") {
				supplied = strings.TrimPrefix(authHdr, "Bearer ")
			}
		}
		if subtle.ConstantTimeCompare([]byte(supplied), []byte(s.sharedSecret)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]any{"ok": false, "error": "unauthorized"})
			return
		}
		next(w, r)
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func decodeJSONBody(r *http.Request, dst any) error {
	defer r.Body.Close()
	body, err := io.ReadAll(io.LimitReader(r.Body, 2<<20))
	if err != nil {
		return err
	}
	return json.Unmarshal(body, dst)
}

func decodeOptionalJSONBody(r *http.Request, dst any) error {
	defer r.Body.Close()
	body, err := io.ReadAll(io.LimitReader(r.Body, 2<<20))
	if err != nil {
		return err
	}
	if len(bytes.TrimSpace(body)) == 0 {
		return nil
	}
	return json.Unmarshal(body, dst)
}

func (s *Server) handlePublicHealth(w http.ResponseWriter, r *http.Request) {
	st := s.epochs.Status()
	code := http.StatusOK
	if st.State == epoch.StateLocked {
		code = http.StatusServiceUnavailable
	}
	writeJSON(w, code, map[string]any{
		"ok":    st.State != epoch.StateLocked,
		"state": st.State,
		"week":  st.CurrentWeek,
	})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "GET required"})
		return
	}
	writeJSON(w, http.StatusOK, s.epochs.Status())
}

func (s *Server) handleVaultSync(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	if err := s.epochs.SyncNow(r.Context()); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{
			"ok":     false,
			"error":  err.Error(),
			"status": s.epochs.Status(),
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":     true,
		"status": s.epochs.Status(),
	})
}

func (s *Server) handleVaultLockdown(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		Reason string `json:"reason"`
	}
	if err := decodeOptionalJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if req.Reason == "" {
		req.Reason = "operator manual lockdown"
	}
	s.epochs.ForceLockdown(req.Reason)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":     true,
		"status": s.epochs.Status(),
	})
}

func (s *Server) handleVolumesHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "GET required"})
		return
	}
	if s.volumes == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "volumes manager unavailable"})
		return
	}
	rep := s.volumes.Health()
	code := http.StatusOK
	if !rep.OK {
		code = http.StatusServiceUnavailable
	}
	writeJSON(w, code, rep)
}

func (s *Server) handleVolumesProvision(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req volumes.ProvisionRequest
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	res, err := s.volumes.Provision(r.Context(), req)
	if err != nil {
		code := http.StatusBadRequest
		if errors.Is(err, epoch.ErrVaultLocked) {
			code = http.StatusServiceUnavailable
		}
		writeJSON(w, code, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, res)
}

func (s *Server) handleVolumesUnlock(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req volumes.UnlockRequest
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	res, err := s.volumes.Unlock(r.Context(), req)
	if err != nil {
		code := http.StatusBadRequest
		if errors.Is(err, epoch.ErrVaultLocked) || errors.Is(err, epoch.ErrServiceLocked) {
			code = http.StatusLocked
		}
		writeJSON(w, code, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, res)
}

func (s *Server) handleVolumesLock(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		VolumeName string `json:"volume_name"`
		ServiceID  string `json:"service_id"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if err := s.volumes.Lock(r.Context(), req.VolumeName, req.ServiceID); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleVolumesCleanup(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		VolumeName string `json:"volume_name"`
		ServiceID  string `json:"service_id"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if err := s.volumes.Cleanup(r.Context(), req.VolumeName, req.ServiceID); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleServiceRegister(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		ServiceID        string   `json:"service_id"`
		ProjectID        string   `json:"project_id"`
		OptedIn          bool     `json:"opted_in"`
		DBRoles          []string `json:"db_roles"`
		DBNames          []string `json:"db_names"`
		RedisUsers       []string `json:"redis_users"`
		RedisPrefixes    []string `json:"redis_prefixes"`
		UpstreamPG       string   `json:"upstream_pg"`
		UpstreamRedis    string   `json:"upstream_redis"`
		EncryptRedisVals *bool    `json:"encrypt_redis_vals,omitempty"`
		UnlockNow        bool     `json:"unlock_now"`
		TTLSeconds       int      `json:"ttl_seconds"`
		Persistent       bool     `json:"persistent"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	encRedis := req.OptedIn
	if req.EncryptRedisVals != nil {
		encRedis = *req.EncryptRedisVals && req.OptedIn
	}
	rec := &crypto.ServiceKeyRecord{
		ServiceID:        req.ServiceID,
		ProjectID:        req.ProjectID,
		OptedIn:          req.OptedIn,
		DBRoles:          req.DBRoles,
		DBNames:          req.DBNames,
		RedisUsers:       req.RedisUsers,
		RedisPrefixes:    req.RedisPrefixes,
		UpstreamPG:       req.UpstreamPG,
		UpstreamRedis:    req.UpstreamRedis,
		EncryptRedisVals: encRedis,
	}
	if err := s.epochs.RegisterService(rec, req.OptedIn); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if s.router != nil {
		s.router.Register(dbproxy.ServiceTarget{
			ServiceID:        rec.ServiceID,
			ProjectID:        rec.ProjectID,
			DBRoles:          req.DBRoles,
			DBNames:          req.DBNames,
			RedisUsers:       req.RedisUsers,
			RedisPrefixes:    req.RedisPrefixes,
			UpstreamPG:       req.UpstreamPG,
			UpstreamRedis:    req.UpstreamRedis,
			OptedIn:          req.OptedIn,
			EncryptRedisVals: encRedis,
		})
	}
	if req.OptedIn && req.UnlockNow {
		ttl := time.Duration(req.TTLSeconds) * time.Second
		if _, uerr := s.epochs.UnlockService(rec.ServiceID, rec.ProjectID, ttl, req.Persistent); uerr != nil {
			writeJSON(w, http.StatusLocked, map[string]any{"ok": false, "error": uerr.Error()})
			return
		}
	}
	opted, allowed, _ := s.epochs.GetServicePolicy(rec.ServiceID)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"service_id": rec.ServiceID,
		"project_id": rec.ProjectID,
		"opted_in":   opted,
		"allowed":    allowed,
	})
}

func (s *Server) handleServiceSubroute(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/v1/services/")
	parts := strings.Split(rest, "/")
	if len(parts) != 2 || parts[0] == "" {
		writeJSON(w, http.StatusNotFound, map[string]any{"ok": false, "error": "invalid service route"})
		return
	}
	serviceID, action := parts[0], parts[1]
	switch action {
	case "policy":
		if r.Method != http.MethodGet {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "GET required"})
			return
		}
		opted, allowed, err := s.epochs.GetServicePolicy(serviceID)
		errStr := ""
		if err != nil {
			errStr = err.Error()
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":         true,
			"service_id": serviceID,
			"opted_in":   opted,
			"allowed":    allowed,
			"reason":     errStr,
		})
	case "unlock":
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
			return
		}
		var req struct {
			ProjectID  string `json:"project_id"`
			TTLSeconds int    `json:"ttl_seconds"`
			Persistent bool   `json:"persistent"`
		}
		if err := decodeOptionalJSONBody(r, &req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		lease, err := s.epochs.UnlockService(serviceID, req.ProjectID, time.Duration(req.TTLSeconds)*time.Second, req.Persistent)
		if err != nil {
			writeJSON(w, http.StatusLocked, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "lease": lease})
	case "lock":
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
			return
		}
		var req struct {
			Reason string `json:"reason"`
		}
		if err := decodeOptionalJSONBody(r, &req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		if err := s.epochs.LockService(serviceID, req.Reason); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "service_id": serviceID, "locked": true})
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{"ok": false, "error": "unknown service action"})
	}
}

func (s *Server) handleTransitEncrypt(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		ServiceID    string `json:"service_id"`
		ProjectID    string `json:"project_id"`
		Context      string `json:"context"`
		Mode         string `json:"mode"` // "persistent" (default) or "epoch"
		PlaintextB64 string `json:"plaintext_b64"`
		AllowOptOut  bool   `json:"allow_opt_out"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if strings.TrimSpace(req.ServiceID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "service_id is required"})
		return
	}
	cleanSvc, verr := crypto.ValidateIdentifier(req.ServiceID)
	if verr != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": verr.Error()})
		return
	}
	req.ServiceID = cleanSvc
	if req.Mode == "" {
		req.Mode = "persistent"
	}
	optedIn, allowed, err := s.epochs.GetServicePolicy(req.ServiceID)
	if !optedIn && err == nil && req.AllowOptOut {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":            true,
			"skipped":       true,
			"plaintext_b64": req.PlaintextB64,
		})
		return
	}
	if !allowed {
		writeJSON(w, http.StatusLocked, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	pt, err := base64.StdEncoding.DecodeString(req.PlaintextB64)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid plaintext_b64"})
		return
	}
	defer crypto.ZeroBytes(pt)

	token, err := s.epochs.EncryptTransit(req.ServiceID, req.ProjectID, req.Context, req.Mode, pt)
	if err != nil {
		code := http.StatusBadRequest
		if errors.Is(err, epoch.ErrVaultLocked) || errors.Is(err, epoch.ErrServiceLocked) {
			code = http.StatusLocked
		}
		writeJSON(w, code, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "token": token})
}

func (s *Server) handleTransitDecrypt(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		ServiceID string `json:"service_id"`
		ProjectID string `json:"project_id"`
		Context   string `json:"context"`
		Token     string `json:"token"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if strings.TrimSpace(req.ServiceID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "service_id is required"})
		return
	}
	cleanSvc, verr := crypto.ValidateIdentifier(req.ServiceID)
	if verr != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": verr.Error()})
		return
	}
	req.ServiceID = cleanSvc
	pt, err := s.epochs.DecryptTransit(req.ServiceID, req.ProjectID, req.Context, req.Token)
	if err != nil {
		code := http.StatusBadRequest
		if errors.Is(err, epoch.ErrVaultLocked) || errors.Is(err, epoch.ErrServiceLocked) {
			code = http.StatusLocked
		}
		writeJSON(w, code, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	defer crypto.ZeroBytes(pt)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":            true,
		"plaintext_b64": base64.StdEncoding.EncodeToString(pt),
	})
}

func (s *Server) handleBackupWrapKey(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		ServiceID string `json:"service_id"`
		ProjectID string `json:"project_id"`
		DEKB64    string `json:"dek_b64"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if strings.TrimSpace(req.ServiceID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "service_id is required"})
		return
	}
	cleanSvc, verr := crypto.ValidateIdentifier(req.ServiceID)
	if verr != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": verr.Error()})
		return
	}
	req.ServiceID = cleanSvc
	dek, err := base64.StdEncoding.DecodeString(req.DEKB64)
	if err != nil || len(dek) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid dek_b64"})
		return
	}
	defer crypto.ZeroBytes(dek)

	wrapped, err := s.epochs.WrapBackupDEK(req.ServiceID, req.ProjectID, dek)
	if err != nil {
		writeJSON(w, http.StatusLocked, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "wrapped_token": wrapped})
}

func (s *Server) handleBackupUnwrapKey(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "POST required"})
		return
	}
	var req struct {
		ServiceID    string `json:"service_id"`
		ProjectID    string `json:"project_id"`
		WrappedToken string `json:"wrapped_token"`
	}
	if err := decodeJSONBody(r, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if strings.TrimSpace(req.ServiceID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "service_id is required"})
		return
	}
	cleanSvc, verr := crypto.ValidateIdentifier(req.ServiceID)
	if verr != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": verr.Error()})
		return
	}
	req.ServiceID = cleanSvc
	dek, err := s.epochs.UnwrapBackupDEK(req.ServiceID, req.ProjectID, req.WrappedToken)
	if err != nil {
		writeJSON(w, http.StatusLocked, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	defer crypto.ZeroBytes(dek)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"dek_b64": base64.StdEncoding.EncodeToString(dek),
	})
}
