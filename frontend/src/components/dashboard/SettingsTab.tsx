import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Database, RotateCcw, ShieldAlert, Upload } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import {
  changePasswordApi,
  downloadDbBackupApi,
  fetchDbBackupInfo,
  restoreDbBackupApi,
  restoreDbBackupUploadApi,
  type DbBackupEntry,
} from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useI18n } from '@/i18n/I18nProvider'

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function formatBackupDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function SettingsTab() {
  const { t } = useI18n()
  const { username, defaultPassword, refresh } = useAuth()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [backupError, setBackupError] = useState<string | null>(null)
  const [backupPath, setBackupPath] = useState<string | null>(null)
  const [backupDir, setBackupDir] = useState<string | null>(null)
  const [backups, setBackups] = useState<DbBackupEntry[]>([])
  const [backingUp, setBackingUp] = useState(false)
  const [restoringName, setRestoringName] = useState<string | null>(null)
  const [uploadRestoring, setUploadRestoring] = useState(false)

  const loadBackupInfo = useCallback(async () => {
    const info = await fetchDbBackupInfo()
    setBackupDir(info.backupDir)
    setBackups(info.backups)
  }, [])

  useEffect(() => {
    loadBackupInfo().catch(() => {
      /* optional */
    })
  }, [loadBackupInfo])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSuccess(null)

    if (newPassword !== confirmPassword) {
      setError(t('settings.passwordMismatch'))
      return
    }

    setSubmitting(true)
    try {
      await changePasswordApi(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSuccess(t('settings.passwordChanged'))
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDbBackup() {
    setBackupError(null)
    setBackupPath(null)
    setBackingUp(true)
    try {
      const { path } = await downloadDbBackupApi()
      setBackupPath(path)
      await loadBackupInfo()
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setBackingUp(false)
    }
  }

  async function handleRestore(name: string) {
    if (!window.confirm(t('settings.dbRestoreConfirm', { name }))) return
    setBackupError(null)
    setRestoringName(name)
    try {
      await restoreDbBackupApi(name)
      window.location.reload()
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : t('common.error'))
      setRestoringName(null)
    }
  }

  async function handleUploadRestore(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!window.confirm(t('settings.dbRestoreUploadConfirm'))) return

    setBackupError(null)
    setUploadRestoring(true)
    try {
      await restoreDbBackupUploadApi(file)
      window.location.reload()
    } catch (err) {
      setBackupError(err instanceof Error ? err.message : t('common.error'))
      setUploadRestoring(false)
    }
  }

  const restoreBusy = restoringName !== null || uploadRestoring

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold">{t('settings.title')}</h1>
        <p className="text-zinc-400 mt-1">{t('settings.subtitle')}</p>
      </div>

      {defaultPassword && (
        <Card className="border-amber-500/20 bg-amber-500/5 p-4 flex gap-3">
          <ShieldAlert className="text-amber-400 shrink-0 mt-0.5" size={20} />
          <div>
            <p className="font-medium text-amber-200">{t('settings.defaultPasswordTitle')}</p>
            <p className="text-sm text-amber-200/80 mt-1">{t('settings.defaultPasswordHint')}</p>
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        <Card className="p-6 space-y-5">
          <div>
            <p className="text-sm text-zinc-500">{t('auth.username')}</p>
            <p className="text-zinc-100 font-medium mt-1">{username ?? 'admin'}</p>
          </div>

          <form onSubmit={onSubmit} className="space-y-4">
            <Input
              label={t('settings.currentPassword')}
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              disabled={submitting}
              required
            />
            <Input
              label={t('settings.newPassword')}
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={submitting}
              hint={t('settings.newPasswordHint')}
              required
            />
            <Input
              label={t('settings.confirmPassword')}
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={submitting}
              error={error ?? undefined}
              required
            />
            {success && <p className="text-sm text-emerald-400">{success}</p>}
            <Button type="submit" disabled={submitting}>
              {submitting ? t('settings.saving') : t('settings.changePassword')}
            </Button>
          </form>
        </Card>

        <Card className="p-6 space-y-4">
          <div>
            <p className="font-medium">{t('settings.dbBackupTitle')}</p>
            <p className="text-sm text-zinc-500 mt-1">{t('settings.dbBackupHint')}</p>
            {backupDir && (
              <p className="text-[11px] text-zinc-600 mt-2 font-mono break-all">
                {t('settings.dbBackupDir')}: {backupDir}/
              </p>
            )}
          </div>

          {backupError && <p className="text-sm text-red-400">{backupError}</p>}
          {backupPath && (
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3 space-y-1">
              <p className="text-sm text-emerald-400">{t('settings.dbBackupSuccess')}</p>
              <p className="text-[11px] text-zinc-400">{t('settings.dbBackupPath')}</p>
              <p className="text-xs font-mono text-emerald-200/90 break-all">{backupPath}</p>
            </div>
          )}

          <Button type="button" variant="secondary" disabled={backingUp || restoreBusy} onClick={handleDbBackup}>
            <Database size={16} />
            {backingUp ? t('settings.dbBackupCreating') : t('settings.dbBackup')}
          </Button>

          <div className="border-t border-white/5 pt-4 space-y-3">
            <div>
              <p className="font-medium text-sm">{t('settings.dbRestoreTitle')}</p>
              <p className="text-xs text-zinc-500 mt-1">{t('settings.dbRestoreHint')}</p>
            </div>

            {backups.length === 0 ? (
              <p className="text-xs text-zinc-600">{t('settings.dbRestoreEmpty')}</p>
            ) : (
              <ul className="max-h-44 overflow-y-auto space-y-2 pr-1">
                {backups.map((item) => (
                  <li
                    key={item.name}
                    className="rounded-lg border border-white/5 bg-black/20 p-2.5 space-y-2"
                  >
                    <div className="min-w-0">
                      <p className="text-xs font-mono text-zinc-300 break-all">{item.name}</p>
                      <p className="text-[10px] text-zinc-600 mt-0.5">
                        {formatBackupDate(item.createdAt)} · {formatBytes(item.sizeBytes)}
                      </p>
                      <p className="text-[10px] text-zinc-700 font-mono break-all mt-0.5">{item.path}</p>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      className="w-full"
                      disabled={restoreBusy || backingUp}
                      onClick={() => handleRestore(item.name)}
                    >
                      <RotateCcw size={14} />
                      {restoringName === item.name ? t('settings.dbRestoring') : t('settings.dbRestore')}
                    </Button>
                  </li>
                ))}
              </ul>
            )}

            <input
              ref={uploadRef}
              type="file"
              accept=".db,application/x-sqlite3"
              className="hidden"
              onChange={handleUploadRestore}
            />
            <Button
              type="button"
              variant="secondary"
              className="w-full"
              disabled={restoreBusy || backingUp}
              onClick={() => uploadRef.current?.click()}
            >
              <Upload size={16} />
              {uploadRestoring ? t('settings.dbRestoring') : t('settings.dbRestoreUpload')}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  )
}
