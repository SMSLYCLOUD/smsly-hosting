//go:build !linux

package crypto

import "fmt"

// HardenProcessMemory is a no-op outside Linux, returning an informational warning.
func HardenProcessMemory(strictMlock bool) []string {
	if strictMlock {
		panic("strict mlockall is only supported on Linux")
	}
	return []string{fmt.Sprintf("mlockall skipped on non-Linux platform")}
}
