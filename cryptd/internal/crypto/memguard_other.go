//go:build !linux

package crypto

// HardenProcessMemory is a no-op outside Linux, returning an informational warning.
func HardenProcessMemory(strictMlock bool) []string {
	if strictMlock {
		panic("strict mlockall is only supported on Linux")
	}
	return []string{"mlockall skipped on non-Linux platform"}
}
