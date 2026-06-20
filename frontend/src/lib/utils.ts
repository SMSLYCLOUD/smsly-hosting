import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function slugify(value: string, maxLen = 63): string {
  let slug = value
    .trim()
    .replace(/[^a-zA-Z0-9_.-]/g, '-')
    .replace(/-{2,}/g, '-')
    .toLowerCase()
    .replace(/^[-_.]+/, '')
    .replace(/[-_.]+$/, '')
  if (!slug) return ''
  slug = slug.slice(0, maxLen).replace(/[-_.]+$/, '')
  return /^[a-z0-9]/.test(slug) && /[a-z0-9]$/.test(slug) ? slug : slug.replace(/^[-_.]+/, '').replace(/[-_.]+$/, '')
}
