import { type SVGAttributes } from 'react'

interface Props extends SVGAttributes<SVGSVGElement> {
  size?: number
}

export function BrandIcon({ size = 24, className, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <defs>
        <linearGradient id="raven-bg" x1="0" y1="0" x2="100" y2="100">
          <stop offset="0%" stopColor="#0B1020" />
          <stop offset="100%" stopColor="#172033" />
        </linearGradient>
        <linearGradient id="raven-border" x1="0" y1="0" x2="100" y2="100">
          <stop offset="0%" stopColor="#8B7CFF" stopOpacity="0.8" />
          <stop offset="100%" stopColor="#43D6C5" stopOpacity="0.4" />
        </linearGradient>
        <linearGradient id="wing-top" x1="20" y1="20" x2="60" y2="50">
          <stop offset="0%" stopColor="#8B7CFF" />
          <stop offset="100%" stopColor="#6366F1" />
        </linearGradient>
        <linearGradient id="wing-mid" x1="16" y1="36" x2="50" y2="60">
          <stop offset="0%" stopColor="#6366F1" />
          <stop offset="100%" stopColor="#4F46E5" />
        </linearGradient>
        <linearGradient id="head-beak" x1="45" y1="20" x2="84" y2="45">
          <stop offset="0%" stopColor="#F3F4F6" />
          <stop offset="100%" stopColor="#C7D2FE" />
        </linearGradient>
        <linearGradient id="body-chest" x1="40" y1="40" x2="75" y2="75">
          <stop offset="0%" stopColor="#8B7CFF" />
          <stop offset="100%" stopColor="#43D6C5" />
        </linearGradient>
      </defs>

      {/* Rounded badge container */}
      <rect width="100" height="100" rx="22" fill="url(#raven-bg)" />
      <rect x="1" y="1" width="98" height="98" rx="21" stroke="url(#raven-border)" strokeWidth="1.5" />

      {/* RAVEN In Flight (Geometric Origami Silhouette) */}
      {/* Upper Wing */}
      <path d="M52 24 L20 22 L42 44 Z" fill="url(#wing-top)" />
      
      {/* Mid Feather */}
      <path d="M42 44 L16 38 L36 54 Z" fill="url(#wing-mid)" />
      
      {/* Lower Wing Feather */}
      <path d="M36 54 L18 56 L38 64 Z" fill="#4338CA" />

      {/* Tail Feathers */}
      <path d="M48 68 L32 82 L44 76 Z" fill="#3730A3" />

      {/* Head & Sharp Beak */}
      <path d="M52 24 L82 36 L58 46 Z" fill="url(#head-beak)" />

      {/* Glowing Cyan Eye Accent */}
      <circle cx="62" cy="34" r="2.5" fill="#43D6C5" />

      {/* Core Body Heart */}
      <path d="M52 24 L42 44 L58 46 Z" fill="#7C3AED" />

      {/* Forward Chest & Flank */}
      <path d="M58 46 L82 36 L66 60 L48 68 Z" fill="url(#body-chest)" opacity="0.95" />
    </svg>
  )
}

export function BrandMark({ size = 24, className, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <defs>
        <linearGradient id="raven-mark-bg" x1="0" y1="0" x2="100" y2="100">
          <stop offset="0%" stopColor="#0B1020" />
          <stop offset="100%" stopColor="#172033" />
        </linearGradient>
        <linearGradient id="raven-mark-border" x1="0" y1="0" x2="100" y2="100">
          <stop offset="0%" stopColor="#8B7CFF" />
          <stop offset="100%" stopColor="#43D6C5" />
        </linearGradient>
        <linearGradient id="mark-wing-top" x1="20" y1="20" x2="60" y2="50">
          <stop offset="0%" stopColor="#8B7CFF" />
          <stop offset="100%" stopColor="#6366F1" />
        </linearGradient>
        <linearGradient id="mark-body" x1="40" y1="40" x2="75" y2="75">
          <stop offset="0%" stopColor="#8B7CFF" />
          <stop offset="100%" stopColor="#43D6C5" />
        </linearGradient>
      </defs>

      {/* Circular background */}
      <circle cx="50" cy="50" r="48" fill="url(#raven-mark-bg)" />
      <circle cx="50" cy="50" r="47.5" stroke="url(#raven-mark-border)" strokeWidth="1.5" />

      {/* RAVEN Silhouette */}
      <path d="M52 24 L20 22 L42 44 Z" fill="url(#mark-wing-top)" />
      <path d="M42 44 L16 38 L36 54 Z" fill="#6366F1" />
      <path d="M36 54 L18 56 L38 64 Z" fill="#4338CA" />
      <path d="M48 68 L32 82 L44 76 Z" fill="#3730A3" />
      <path d="M52 24 L82 36 L58 46 Z" fill="#F3F4F6" />
      <circle cx="62" cy="34" r="2.5" fill="#43D6C5" />
      <path d="M52 24 L42 44 L58 46 Z" fill="#7C3AED" />
      <path d="M58 46 L82 36 L66 60 L48 68 Z" fill="url(#mark-body)" opacity="0.95" />
    </svg>
  )
}
