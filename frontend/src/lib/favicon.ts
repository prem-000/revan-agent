const RAVEN_SVG_ENCODED = "%3Csvg%20xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'%20viewBox%3D'0%200%20100%20100'%3E%3Cdefs%3E%3ClinearGradient%20id%3D'bg'%20x1%3D'0'%20y1%3D'0'%20x2%3D'100'%20y2%3D'100'%3E%3Cstop%20offset%3D'0%25'%20stop-color%3D'%230B1020'%2F%3E%3Cstop%20offset%3D'100%25'%20stop-color%3D'%23172033'%2F%3E%3C%2FlinearGradient%3E%3ClinearGradient%20id%3D'bd'%20x1%3D'0'%20y1%3D'0'%20x2%3D'100'%20y2%3D'100'%3E%3Cstop%20offset%3D'0%25'%20stop-color%3D'%238B7CFF'%2F%3E%3Cstop%20offset%3D'100%25'%20stop-color%3D'%2343D6C5'%2F%3E%3C%2FlinearGradient%3E%3ClinearGradient%20id%3D'wt'%20x1%3D'20'%20y1%3D'20'%20x2%3D'60'%20y2%3D'50'%3E%3Cstop%20offset%3D'0%25'%20stop-color%3D'%238B7CFF'%2F%3E%3Cstop%20offset%3D'100%25'%20stop-color%3D'%236366F1'%2F%3E%3C%2FlinearGradient%3E%3ClinearGradient%20id%3D'bc'%20x1%3D'40'%20y1%3D'40'%20x2%3D'75'%20y2%3D'75'%3E%3Cstop%20offset%3D'0%25'%20stop-color%3D'%238B7CFF'%2F%3E%3Cstop%20offset%3D'100%25'%20stop-color%3D'%2343D6C5'%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Crect%20width%3D'100'%20height%3D'100'%20rx%3D'22'%20fill%3D'url(%23bg)'%2F%3E%3Crect%20x%3D'1'%20y%3D'1'%20width%3D'98'%20height%3D'98'%20rx%3D'21'%20stroke%3D'url(%23bd)'%20stroke-width%3D'2'%20stroke-opacity%3D'0.7'%2F%3E%3Cpath%20d%3D'M52%2024%20L20%2022%20L42%2044%20Z'%20fill%3D'url(%23wt)'%2F%3E%3Cpath%20d%3D'M42%2044%20L16%2038%20L36%2054%20Z'%20fill%3D'%236366F1'%2F%3E%3Cpath%20d%3D'M36%2054%20L18%2056%20L38%2064%20Z'%20fill%3D'%234338CA'%2F%3E%3Cpath%20d%3D'M48%2068%20L32%2082%20L44%2076%20Z'%20fill%3D'%233730A3'%2F%3E%3Cpath%20d%3D'M52%2024%20L82%2036%20L58%2046%20Z'%20fill%3D'%23F3F4F6'%2F%3E%3Ccircle%20cx%3D'62'%20cy%3D'34'%20r%3D'3'%20fill%3D'%2343D6C5'%2F%3E%3Cpath%20d%3D'M52%2024%20L42%2044%20L58%2046%20Z'%20fill%3D'%237C3AED'%2F%3E%3Cpath%20d%3D'M58%2046%20L82%2036%20L66%2060%20L48%2068%20Z'%20fill%3D'url(%23bc)'%20opacity%3D'0.95'%2F%3E%3C%2Fsvg%3E"

const LIGHT_SVG = RAVEN_SVG_ENCODED
const DARK_SVG = RAVEN_SVG_ENCODED
const LIGHT_APPLE_SVG = RAVEN_SVG_ENCODED
const DARK_APPLE_SVG = RAVEN_SVG_ENCODED

const LIGHT_THEME_COLOR = '#0B1020'
const DARK_THEME_COLOR = '#0B1020'

export function setFavicon(theme: 'light' | 'dark') {
  if (typeof document === 'undefined') return
  const href = `data:image/svg+xml,${theme === 'light' ? LIGHT_SVG : DARK_SVG}`

  // Remove existing favicon links to avoid conflicts with HTML media-query variants
  document.querySelectorAll('link[rel="icon"]').forEach(el => el.remove())

  // Add a single favicon link matching the current theme
  const link = document.createElement('link')
  link.rel = 'icon'
  link.type = 'image/svg+xml'
  link.href = href
  document.head.appendChild(link)

  // Update apple-touch-icon for iOS home screen
  const appleHref = `data:image/svg+xml,${theme === 'light' ? LIGHT_APPLE_SVG : DARK_APPLE_SVG}`
  const existingApple = document.querySelector('link[rel="apple-touch-icon"]')
  if (existingApple) {
    existingApple.setAttribute('href', appleHref)
  }

  // Also update theme-color meta tags
  const color = theme === 'light' ? LIGHT_THEME_COLOR : DARK_THEME_COLOR
  document.querySelectorAll('meta[name="theme-color"]').forEach(el => el.setAttribute('content', color))
}
