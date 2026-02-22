"use client"

import * as React from "react"

const TOAST_LIMIT = 5
const TOAST_REMOVE_DELAY = 5000

export type ToastVariant = "default" | "destructive" | "success" | "warning" | "info"

type ToasterToast = {
    id: string
    title?: string
    description?: string
    action?: React.ReactNode
    variant?: ToastVariant
    /** Override auto-dismiss duration in ms. Set to 0 to disable auto-dismiss. */
    duration?: number
}

const actionTypes = {
    ADD_TOAST: "ADD_TOAST",
    UPDATE_TOAST: "UPDATE_TOAST",
    DISMISS_TOAST: "DISMISS_TOAST",
    REMOVE_TOAST: "REMOVE_TOAST",
} as const

let count = 0

function genId() {
    count = (count + 1) % Number.MAX_SAFE_INTEGER
    return count.toString()
}

type ActionType = typeof actionTypes

type Action =
    | { type: ActionType["ADD_TOAST"]; toast: ToasterToast }
    | { type: ActionType["UPDATE_TOAST"]; toast: Partial<ToasterToast> }
    | { type: ActionType["DISMISS_TOAST"]; toastId?: string }
    | { type: ActionType["REMOVE_TOAST"]; toastId?: string }

interface State {
    toasts: ToasterToast[]
}

const toastTimeouts = new Map<string, ReturnType<typeof setTimeout>>()

const addToRemoveQueue = (toastId: string, duration?: number) => {
    if (toastTimeouts.has(toastId)) {
        return
    }

    const delay = duration ?? TOAST_REMOVE_DELAY
    if (delay <= 0) return  // Duration 0 means sticky (no auto-dismiss)

    const timeout = setTimeout(() => {
        toastTimeouts.delete(toastId)
        dispatch({ type: "REMOVE_TOAST", toastId })
    }, delay)

    toastTimeouts.set(toastId, timeout)
}

const clearFromRemoveQueue = (toastId: string) => {
    const timeout = toastTimeouts.get(toastId)
    if (timeout) {
        clearTimeout(timeout)
        toastTimeouts.delete(toastId)
    }
}

export const reducer = (state: State, action: Action): State => {
    switch (action.type) {
        case "ADD_TOAST":
            return {
                ...state,
                toasts: [action.toast, ...state.toasts].slice(0, TOAST_LIMIT),
            }

        case "UPDATE_TOAST":
            return {
                ...state,
                toasts: state.toasts.map((t) =>
                    t.id === action.toast.id ? { ...t, ...action.toast } : t
                ),
            }

        case "DISMISS_TOAST": {
            const { toastId } = action

            if (toastId) {
                addToRemoveQueue(toastId)
            } else {
                state.toasts.forEach((toast) => {
                    addToRemoveQueue(toast.id)
                })
            }

            return {
                ...state,
                toasts: state.toasts.map((t) =>
                    t.id === toastId || toastId === undefined
                        ? { ...t }
                        : t
                ),
            }
        }

        case "REMOVE_TOAST":
            if (action.toastId === undefined) {
                return { ...state, toasts: [] }
            }
            return {
                ...state,
                toasts: state.toasts.filter((t) => t.id !== action.toastId),
            }
    }
}

const listeners: Array<(state: State) => void> = []

let memoryState: State = { toasts: [] }

function dispatch(action: Action) {
    memoryState = reducer(memoryState, action)
    listeners.forEach((listener) => {
        listener(memoryState)
    })
}

type Toast = Omit<ToasterToast, "id">

function toast({ ...props }: Toast) {
    const id = genId()

    const update = (props: ToasterToast) =>
        dispatch({ type: "UPDATE_TOAST", toast: { ...props, id } })

    const dismiss = () => {
        clearFromRemoveQueue(id)
        dispatch({ type: "REMOVE_TOAST", toastId: id })
    }

    dispatch({
        type: "ADD_TOAST",
        toast: { ...props, id },
    })

    // Auto-remove after delay by default (can still be dismissed manually via X button).
    addToRemoveQueue(id, props.duration)

    return { id, dismiss, update }
}

// ─── Convenience helpers (import these directly for quick one-liners) ───────
toast.success = (title: string, description?: string) =>
    toast({ title, description, variant: "success" })

toast.error = (title: string, description?: string) =>
    toast({ title, description, variant: "destructive" })

toast.warning = (title: string, description?: string) =>
    toast({ title, description, variant: "warning" })

toast.info = (title: string, description?: string) =>
    toast({ title, description, variant: "info" })

function useToast() {
    const [state, setState] = React.useState<State>(memoryState)

    React.useEffect(() => {
        listeners.push(setState)
        return () => {
            const index = listeners.indexOf(setState)
            if (index > -1) {
                listeners.splice(index, 1)
            }
        }
    }, [state])

    return {
        ...state,
        toast,
        dismiss: (toastId?: string) => {
            if (toastId) {
                clearFromRemoveQueue(toastId)
                dispatch({ type: "REMOVE_TOAST", toastId })
                return
            }

            // Clear all pending removals and remove all toasts immediately.
            toastTimeouts.forEach((t) => clearTimeout(t))
            toastTimeouts.clear()
            dispatch({ type: "REMOVE_TOAST" })
        },
    }
}

export { useToast, toast }
