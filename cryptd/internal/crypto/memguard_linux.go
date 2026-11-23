//go:build linux

package crypto

import (
	"fmt"
	"syscall"
)

// HardenProcessMemory disables core dumps and attempts mlockall(MCL_CURRENT|MCL_FUTURE)
// so derived keys are kept out of swap/core files.
func HardenProcessMemory(strictMlock bool) []string {
	var warnings []string

	if err := syscall.Setrlimit(syscall.RLIMIT_CORE, &syscall.Rlimit{Cur: 0, Max: 0}); err != nil {
		warnings = append(warnings, fmt.Sprintf("RLIMIT_CORE=0 failed: %v", err))
	}

	const mclCurrent = 1
	const mclFuture = 2
	if err := syscall.Mlockall(mclCurrent | mclFuture); err != nil {
		msg := fmt.Sprintf("mlockall failed (keys may not be locked out of swap): %v", err)
		if strictMlock {
			panic(msg)
		}
		warnings = append(warnings, msg)
	}
	return warnings
}
