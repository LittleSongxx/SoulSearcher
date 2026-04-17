'use client'

import { useState, useEffect } from 'react'

export type OSType = 'mac' | 'windows' | 'linux'

export function useOS(): OSType {
  const [os, setOs] = useState<OSType>('windows')

  useEffect(() => {
    const ua = navigator.userAgent.toLowerCase()
    const platform = (navigator.platform || '').toLowerCase()

    if (platform.startsWith('mac') || ua.includes('macintosh') || ua.includes('mac os')) {
      setOs('mac')
    } else if (platform.startsWith('linux') || ua.includes('linux')) {
      setOs('linux')
    } else {
      setOs('windows')
    }
  }, [])

  return os
}
