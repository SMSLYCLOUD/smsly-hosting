package dbproxy

import (
	"io"
	"net"
	"strings"
	"sync"
)

// ServiceTarget defines how a DB role/DB name/Redis prefix maps to a cryptographic service identity.
type ServiceTarget struct {
	ServiceID        string   `json:"service_id"`
	ProjectID        string   `json:"project_id"`
	DBRoles          []string `json:"db_roles,omitempty"`
	DBNames          []string `json:"db_names,omitempty"`
	RedisUsers       []string `json:"redis_users,omitempty"`
	RedisPrefixes    []string `json:"redis_prefixes,omitempty"`
	UpstreamPG       string   `json:"upstream_pg,omitempty"`
	UpstreamRedis    string   `json:"upstream_redis,omitempty"`
	OptedIn          bool     `json:"opted_in"`
	EncryptRedisVals bool     `json:"encrypt_redis_vals"`
}

// Router resolves database / Redis endpoints to service targets and tracks live sockets per service
// so locking a service or expiring the vault grace period severs open DB/Redis TCP sessions instantly.
type Router struct {
	mu            sync.RWMutex
	defaultTarget ServiceTarget
	byService     map[string]ServiceTarget
	byPGUser      map[string]string
	byPGDB        map[string]string
	byRedisUser   map[string]string
	redisPrefixes []prefixBinding

	connMu      sync.Mutex
	activeConns map[string]map[io.Closer]struct{}
}

type prefixBinding struct {
	prefix    string
	serviceID string
}

// NewRouter initializes a thread-safe database/Redis service router and connection tracker.
func NewRouter(defaultTarget ServiceTarget) *Router {
	if defaultTarget.ServiceID == "" {
		defaultTarget.ServiceID = "paas-control-plane"
	}
	if defaultTarget.ProjectID == "" {
		defaultTarget.ProjectID = "control-plane"
	}
	return &Router{
		defaultTarget: defaultTarget,
		byService:     make(map[string]ServiceTarget),
		byPGUser:      make(map[string]string),
		byPGDB:        make(map[string]string),
		byRedisUser:   make(map[string]string),
		activeConns:   make(map[string]map[io.Closer]struct{}),
	}
}

// Register binds DB roles, DB names, Redis ACL users, and key prefixes to target.ServiceID.
func (r *Router) Register(target ServiceTarget) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if target.ProjectID == "" {
		target.ProjectID = "default"
	}
	r.byService[target.ServiceID] = target
	for _, u := range target.DBRoles {
		u = strings.TrimSpace(u)
		if u != "" {
			r.byPGUser[u] = target.ServiceID
		}
	}
	for _, d := range target.DBNames {
		d = strings.TrimSpace(d)
		if d != "" {
			r.byPGDB[d] = target.ServiceID
		}
	}
	for _, ru := range target.RedisUsers {
		ru = strings.TrimSpace(ru)
		if ru != "" {
			r.byRedisUser[ru] = target.ServiceID
		}
	}
	// Replace any existing prefix bindings belonging to this service.
	filtered := r.redisPrefixes[:0]
	for _, pb := range r.redisPrefixes {
		if pb.serviceID != target.ServiceID {
			filtered = append(filtered, pb)
		}
	}
	for _, p := range target.RedisPrefixes {
		p = strings.TrimSpace(p)
		if p != "" {
			filtered = append(filtered, prefixBinding{prefix: p, serviceID: target.ServiceID})
		}
	}
	r.redisPrefixes = filtered
}

// Unregister removes service routing entries.
func (r *Router) Unregister(serviceID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.byService, serviceID)
	for k, v := range r.byPGUser {
		if v == serviceID {
			delete(r.byPGUser, k)
		}
	}
	for k, v := range r.byPGDB {
		if v == serviceID {
			delete(r.byPGDB, k)
		}
	}
	for k, v := range r.byRedisUser {
		if v == serviceID {
			delete(r.byRedisUser, k)
		}
	}
	filtered := r.redisPrefixes[:0]
	for _, pb := range r.redisPrefixes {
		if pb.serviceID != serviceID {
			filtered = append(filtered, pb)
		}
	}
	r.redisPrefixes = filtered
}

// ResolvePostgres returns the ServiceTarget for a Postgres StartupMessage user/database pair.
func (r *Router) ResolvePostgres(pgUser, pgDatabase string) ServiceTarget {
	r.mu.RLock()
	defer r.mu.RUnlock()

	if svcID, ok := r.byPGUser[pgUser]; ok {
		if tgt, exists := r.byService[svcID]; exists {
			return tgt
		}
	}
	if svcID, ok := r.byPGDB[pgDatabase]; ok {
		if tgt, exists := r.byService[svcID]; exists {
			return tgt
		}
	}
	return r.defaultTarget
}

// ResolveRedisUser returns the ServiceTarget mapped to a Redis ACL username.
func (r *Router) ResolveRedisUser(username string) (ServiceTarget, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	if svcID, ok := r.byRedisUser[username]; ok {
		if tgt, exists := r.byService[svcID]; exists {
			return tgt, true
		}
	}
	return r.defaultTarget, false
}

// ResolveRedisKey returns the ServiceTarget whose registered key prefix matches keyName.
func (r *Router) ResolveRedisKey(keyName string) (ServiceTarget, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var bestPrefix string
	var bestSvc string
	for _, pb := range r.redisPrefixes {
		if strings.HasPrefix(keyName, pb.prefix) && len(pb.prefix) > len(bestPrefix) {
			bestPrefix = pb.prefix
			bestSvc = pb.serviceID
		}
	}
	if bestSvc != "" {
		if tgt, exists := r.byService[bestSvc]; exists {
			return tgt, true
		}
	}
	return r.defaultTarget, false
}

// LookupService fetches a registered ServiceTarget by ID or falls back to defaultTarget.
func (r *Router) LookupService(serviceID string) (ServiceTarget, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if tgt, ok := r.byService[serviceID]; ok {
		return tgt, true
	}
	return r.defaultTarget, false
}

// TrackConn associates a live client/upstream pair with serviceID for instant lock termination.
func (r *Router) TrackConn(serviceID string, c io.Closer) {
	if c == nil {
		return
	}
	r.connMu.Lock()
	defer r.connMu.Unlock()
	set := r.activeConns[serviceID]
	if set == nil {
		set = make(map[io.Closer]struct{})
		r.activeConns[serviceID] = set
	}
	set[c] = struct{}{}
}

// UntrackConn removes c when closed normally.
func (r *Router) UntrackConn(serviceID string, c io.Closer) {
	if c == nil {
		return
	}
	r.connMu.Lock()
	defer r.connMu.Unlock()
	if set := r.activeConns[serviceID]; set != nil {
		delete(set, c)
		if len(set) == 0 {
			delete(r.activeConns, serviceID)
		}
	}
}

// RebindConn moves a tracked socket to a new serviceID after Postgres startup or Redis AUTH.
func (r *Router) RebindConn(oldServiceID, newServiceID string, c io.Closer) {
	if oldServiceID == newServiceID || c == nil {
		return
	}
	r.connMu.Lock()
	defer r.connMu.Unlock()
	if oldSet := r.activeConns[oldServiceID]; oldSet != nil {
		delete(oldSet, c)
		if len(oldSet) == 0 {
			delete(r.activeConns, oldServiceID)
		}
	}
	newSet := r.activeConns[newServiceID]
	if newSet == nil {
		newSet = make(map[io.Closer]struct{})
		r.activeConns[newServiceID] = newSet
	}
	newSet[c] = struct{}{}
}

// CloseServiceConns forcefully terminates every open DB/Redis connection belonging to serviceID.
func (r *Router) CloseServiceConns(serviceID string) int {
	r.connMu.Lock()
	set := r.activeConns[serviceID]
	delete(r.activeConns, serviceID)
	r.connMu.Unlock()

	count := 0
	for c := range set {
		_ = c.Close()
		count++
	}
	return count
}

// CloseAllConns terminates every live proxied DB and Redis socket across all services.
func (r *Router) CloseAllConns() int {
	r.connMu.Lock()
	all := r.activeConns
	r.activeConns = make(map[string]map[io.Closer]struct{})
	r.connMu.Unlock()

	count := 0
	for _, set := range all {
		for c := range set {
			_ = c.Close()
			count++
		}
	}
	return count
}

type connPair struct {
	a net.Conn
	b net.Conn
}

func (p connPair) Close() error {
	if p.a != nil {
		_ = p.a.Close()
	}
	if p.b != nil {
		_ = p.b.Close()
	}
	return nil
}
