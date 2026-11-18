import { PageHeader } from '@/components/ui/PageHeader'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Database, KeyRound, RotateCcw, ShieldAlert, Upload } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { AppIcon } from '@/components/ui/AppIcon'
import {
  changePasswordApi,
  disable2faApi,
  downloadDbBackupApi,
  enable2faApi,
  fetchDbBackupInfo,
  restoreDbBackupApi,
  restoreDbBackupUploadApi,
  setup2faApi,
  type DbBackupEntry,
} from '@/lib/api'
import { QrImage } from '@/components/ui/QrImage'
import { useAuth } from '@/lib/auth-context'
import { useI18n } from '@/i18n/i18n-context'

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
  const { username, defaultPassword, twoFactorEnabled, refresh } = useAuth()
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

  // 2FA state
  const [settingUp2fa, setSettingUp2fa] = useState(false)
  const [totpSetup, setTotpSetup] = useState<{ secret: string; otpauthUri: string } | null>(null)
  const [enableCode, setEnableCode] = useState('')
  const [totpError, setTotpError] = useState<string | null>(null)
  const [totpSuccess, setTotpSuccess] = useState<string | null>(null)
  const [disabling2fa, setDisabling2fa] = useState(false)
  const [disablePassword, setDisablePassword] = useState('')
  const [disableCode, setDisableCode] = useState('')
  const [totpBusy, setTotpBusy] = useState(false)

  async function handleStartSetup2fa() {
    setTotpError(null)
    setTotpSuccess(null)
    setTotpBusy(true)
    try {
      const res = await setup2faApi()
      setTotpSetup(res)
      setSettingUp2fa(true)
    } catch (err) {
      setTotpError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setTotpBusy(false)
    }
  }

  async function handleEnable2fa(e: FormEvent) {
    e.preventDefault()
    setTotpError(null)
    setTotpSuccess(null)
    setTotpBusy(true)
    try {
      await enable2faApi(enableCode.trim())
      setSettingUp2fa(false)
      setTotpSetup(null)
      setEnableCode('')
      setTotpSuccess(t('settings.twoFactorSuccessEnabled'))
      await refresh()
    } catch (err) {
      setTotpError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setTotpBusy(false)
    }
  }

  async function handleDisable2fa(e: FormEvent) {
    e.preventDefault()
    setTotpError(null)
    setTotpSuccess(null)
    setTotpBusy(true)
    try {
      await disable2faApi(disablePassword, disableCode.trim())
      setDisabling2fa(false)
      setDisablePassword('')
      setDisableCode('')
      setTotpSuccess(t('settings.twoFactorSuccessDisabled'))
      await refresh()
    } catch (err) {
      setTotpError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setTotpBusy(false)
    }
  }

  const loadBackupInfo = useCallback(async () => {
    const info = await fetchDbBackupInfo()
    setBackupDir(info.backupDir)
    setBackups(info.backups)
  }, [])

  useEffect(() => {
    let cancelled = false
    void fetchDbBackupInfo().then((info) => {
      if (cancelled) return
      setBackupDir(info.backupDir)
      setBackups(info.backups)
    }).catch(() => { /* optional */ })
    return () => { cancelled = true }
  }, [])

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
    <div className="dashboard-page">
      <PageHeader title={t('settings.title')} subtitle={t('settings.subtitle')} />

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
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-medium">{t('settings.changePassword')}</h2>
              <p className="text-xs text-text-muted mt-1">{t('auth.username')}: <span className="text-zinc-100 font-medium">{username ?? 'admin'}</span></p>
            </div>
            <div className="shrink-0 drop-shadow-[0_0_12px_rgba(212,162,76,0.35)]">
              <AppIcon name="password" size={40} />
            </div>
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

        {/* 2FA Card */}
        <Card className="p-6 space-y-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-medium">{t('settings.twoFactorTitle')}</h2>
                <Badge variant={twoFactorEnabled ? 'success' : 'default'}>
                  {twoFactorEnabled ? t('settings.twoFactorEnabledBadge') : t('settings.twoFactorDisabledBadge')}
                </Badge>
              </div>
              <p className="text-xs text-text-muted mt-1">{t('settings.twoFactorSubtitle')}</p>
            </div>
            <div className="shrink-0 drop-shadow-[0_0_12px_rgba(212,162,76,0.35)]">
              <AppIcon name="two-factor" size={40} />
            </div>
          </div>

          {totpSuccess && <p className="text-sm text-emerald-400">{totpSuccess}</p>}
          {totpError && <p className="text-sm text-rose-400">{totpError}</p>}

          {!twoFactorEnabled && !settingUp2fa && (
            <Button
              type="button"
              variant="secondary"
              disabled={totpBusy}
              onClick={handleStartSetup2fa}
            >
              <KeyRound size={16} />
              {t('settings.twoFactorEnableBtn')}
            </Button>
          )}

          {!twoFactorEnabled && settingUp2fa && totpSetup && (
            <form onSubmit={handleEnable2fa} className="space-y-4 pt-1 border-t border-white/5">
              <div className="space-y-2">
                <p className="text-xs font-medium text-zinc-300">{t('settings.twoFactorScanQr')}</p>
                <div className="flex justify-center p-3 rounded-xl bg-white w-fit mx-auto">
                  <QrImage data={totpSetup.otpauthUri} size={180} />
                </div>
              </div>

              <div className="space-y-1">
                <p className="text-xs text-text-muted">{t('settings.twoFactorManualKey')}</p>
                <code className="block p-2 rounded-lg bg-black/40 text-xs font-mono text-accent-cyan tracking-wider select-all break-all text-center">
                  {totpSetup.secret}
                </code>
              </div>

              <div className="space-y-2">
                <Input
                  label={t('settings.twoFactorEnterCode')}
                  className="font-mono text-center tracking-widest text-lg"
                  autoComplete="one-time-code"
                  placeholder="123456"
                  maxLength={6}
                  value={enableCode}
                  onChange={(e) => setEnableCode(e.target.value.replace(/\D/g, ''))}
                  disabled={totpBusy}
                  required
                />
              </div>

              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  disabled={totpBusy}
                  onClick={() => { setSettingUp2fa(false); setTotpSetup(null); setEnableCode(''); setTotpError(null) }}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  type="submit"
                  disabled={totpBusy || enableCode.length !== 6}
                >
                  {totpBusy ? t('settings.twoFactorActivating') : t('settings.twoFactorActivate')}
                </Button>
              </div>
            </form>
          )}

          {twoFactorEnabled && !disabling2fa && (
            <Button
              type="button"
              variant="secondary"
              className="text-rose-400 hover:text-rose-300"
              disabled={totpBusy}
              onClick={() => { setDisabling2fa(true); setTotpError(null); setTotpSuccess(null) }}
            >
              {t('settings.twoFactorDisableBtn')}
            </Button>
          )}

          {twoFactorEnabled && disabling2fa && (
            <form onSubmit={handleDisable2fa} className="space-y-3 pt-1 border-t border-white/5">
              <p className="text-xs text-zinc-300">{t('settings.twoFactorDisableHint')}</p>
              <Input
                label={t('settings.currentPassword')}
                type="password"
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
                disabled={totpBusy}
                required
              />
              <Input
                label={t('auth.twoFactorCode')}
                className="font-mono text-center tracking-widest text-lg"
                autoComplete="one-time-code"
                placeholder="123456"
                maxLength={6}
                value={disableCode}
                onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, ''))}
                disabled={totpBusy}
                required
              />
              <div className="flex gap-2 pt-1">
                <Button
                  type="button"
                  variant="ghost"
                  disabled={totpBusy}
                  onClick={() => { setDisabling2fa(false); setDisablePassword(''); setDisableCode(''); setTotpError(null) }}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  type="submit"
                  variant="secondary"
                  className="text-rose-400 hover:text-rose-300"
                  disabled={totpBusy || !disablePassword || disableCode.length !== 6}
                >
                  {t('settings.twoFactorDisableBtn')}
                </Button>
              </div>
            </form>
          )}
        </Card>

        <Card className="p-6 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-medium">{t('settings.dbBackupTitle')}</p>
              <p className="text-sm text-text-muted mt-1">{t('settings.dbBackupHint')}</p>
              {backupDir && (
                <p className="text-[11px] text-text-muted mt-2 font-mono break-all">
                  {t('settings.dbBackupDir')}: {backupDir}/
                </p>
              )}
            </div>
            <div className="shrink-0 drop-shadow-[0_0_12px_rgba(212,162,76,0.35)]">
              <AppIcon name="backup" size={40} />
            </div>
          </div>

          {backupError && <p className="text-sm text-rose-400">{backupError}</p>}
          {backupPath && (
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3 space-y-1">
              <p className="text-sm text-emerald-400">{t('settings.dbBackupSuccess')}</p>
              <p className="text-[11px] text-zinc-400">{t('settings.dbBackupPath')}</p>
              <p className="text-xs font-mono text-emerald-300/90 break-all">{backupPath}</p>
            </div>
          )}

          <Button type="button" variant="secondary" disabled={backingUp || restoreBusy} onClick={handleDbBackup}>
            <Database size={16} />
            {backingUp ? t('settings.dbBackupCreating') : t('settings.dbBackup')}
          </Button>

          <div className="border-t border-white/5 pt-4 space-y-3">
            <div>
              <p className="font-medium text-sm">{t('settings.dbRestoreTitle')}</p>
              <p className="text-xs text-text-muted mt-1">{t('settings.dbRestoreHint')}</p>
            </div>

            {backups.length === 0 ? (
              <div className="empty-state rounded-xl border border-dashed border-white/10 p-4"><Database size={24} aria-hidden="true" /><p className="text-xs">{t('settings.dbRestoreEmpty')}</p></div>
            ) : (
              <ul className="max-h-44 overflow-y-auto space-y-2 pr-1">
                {backups.map((item) => (
                  <li
                    key={item.name}
                    className="rounded-lg surface-panel p-2.5 space-y-2"
                  >
                    <div className="min-w-0">
                      <p className="text-xs font-mono text-zinc-300 break-all">{item.name}</p>
                      <p className="text-[10px] text-text-muted mt-0.5">
                        {formatBackupDate(item.createdAt)} · {formatBytes(item.sizeBytes)}
                      </p>
                      <p className="text-[10px] text-text-muted font-mono break-all mt-0.5">{item.path}</p>
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
