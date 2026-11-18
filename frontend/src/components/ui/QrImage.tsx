import { useEffect, useState } from 'react'
import QRCode from 'qrcode'

export function QrImage({
  data,
  size = 180,
  className,
}: {
  data: string
  size?: number
  className?: string
}) {
  const [dataUrl, setDataUrl] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    if (!data) {
      setDataUrl(null)
      return
    }
    QRCode.toDataURL(data, {
      width: size,
      margin: 1,
      color: {
        dark: '#000000',
        light: '#ffffff',
      },
      errorCorrectionLevel: 'M',
    })
      .then((url) => {
        if (active) setDataUrl(url)
      })
      .catch(() => {
        if (active) setDataUrl(null)
      })
    return () => {
      active = false
    }
  }, [data, size])

  if (!dataUrl) {
    return (
      <div
        style={{ width: size, height: size }}
        className="flex items-center justify-center rounded-xl bg-white/5 animate-pulse text-xs text-text-muted"
      >
        ...
      </div>
    )
  }

  return (
    <img
      src={dataUrl}
      alt="QR Code"
      width={size}
      height={size}
      className={className ?? 'rounded-xl bg-white p-2 object-contain'}
    />
  )
}
