import { PageHeader } from '@/components/ui/PageHeader'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import {
  ChevronDown,
  ChevronUp,
  CircleCheck,
  ExternalLink,
  KeyRound,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Upload,
} from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { AppIcon } from '@/components/ui/AppIcon'
import {
  applySystemUpdateApi,
  changePasswordApi,
  checkSystemUpdateApi,
  disable2faApi,
  downloadDbBackupApi,
  enable2faApi,
  fetchAuthMe,
  fetchDbBackupInfo,
  restoreDbBackupApi,
  restoreDbBackupUploadApi,
  setup2faApi,
  type DbBackupEntry,
  type SystemUpdateInfo,
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

  // Password state
  const [showPasswordForm, setShowPasswordForm] = useState(!!defaultPassword)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null)
  const [submittingPassword, setSubmittingPassword] = useState(false)

  // DB Backup state
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

  // System Update state
  const [updateInfo, setUpdateInfo] = useState<SystemUpdateInfo | null>(null)
  const [checkingUpdate, setCheckingUpdate] = useState(false)
  const [updateError, setUpdateError] = useState<string | null>(null)
  const [applyingUpdate, setApplyingUpdate] = useState(false)
  const [updateNotice, setUpdateNotice] = useState<string | null>(null)

  const handleCheckUpdate = useCallback(async (force = false) => {
    setCheckingUpdate(true)
    setUpdateError(null)
    try {
      const data = await checkSystemUpdateApi(force)
      setUpdateInfo(data)
    } catch (err: any) {
      setUpdateError(err?.detail || err?.message || 'Failed to check updates')
    } finally {
      setCheckingUpdate(false)
    }
  }, [])

  useEffect(() => {
    void handleCheckUpdate(false)
  }, [handleCheckUpdate])

  async function handleApplyUpdate() {
    if (!updateInfo?.latestVersion) return
    const msg = t('settings.updateConfirm', { version: updateInfo.latestVersion })
    if (!window.confirm(msg)) return

    setApplyingUpdate(true)
    setUpdateError(null)
    setUpdateNotice(t('settings.updateStarted'))

    try {
      const res = await applySystemUpdateApi(updateInfo.latestVersion)
      if (!res.ok) {
        setApplyingUpdate(false)
        setUpdateError(res.message || 'Update failed to start')
        return
      }
      let attempts = 0
      const timer = setInterval(async () => {
        attempts++
        try {
          const res = await fetchAuthMe()
          if (res && attempts > 2) {
            clearInterval(timer)
            window.location.reload()
          }
        } catch {
          // restarting
        }
        if (attempts > 60) {
          clearInterval(timer)
          setApplyingUpdate(false)
          setUpdateError('Update timeout. Please refresh manually or check server logs.')
        }
      }, 2000)
    } catch (err: any) {
      setApplyingUpdate(false)
      setUpdateError(err?.detail || err?.message || 'Failed to trigger update')
    }
  }

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

  async function onPasswordSubmit(event: FormEvent) {
    event.preventDefault()
    setPasswordError(null)
    setPasswordSuccess(null)

    if (newPassword !== confirmPassword) {
      setPasswordError(t('settings.passwordMismatch'))
      return
    }

    setSubmittingPassword(true)
    try {
      await changePasswordApi(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setPasswordSuccess(t('settings.passwordChanged'))
      await refresh()
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setSubmittingPassword(false)
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
    <div className="dashboard-page max-w-4xl space-y-6">
      <PageHeader title={t('settings.title')} subtitle={t('settings.subtitle')} />

      {/* Top banner: defaultPassword alert */}
      {defaultPassword && (
        <div className="px-3.5 py-2.5 rounded-xl border border-amber-500/20 bg-amber-500/5 flex items-center gap-2.5 text-xs text-amber-200">
          <ShieldAlert className="text-amber-400 shrink-0" size={16} />
          <div className="flex-1 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1">
            <span className="font-medium text-amber-200">{t('settings.defaultPasswordTitle')}</span>
            <span className="text-amber-200/80">{t('settings.defaultPasswordHint')}</span>
          </div>
        </div>
      )}

      {/* Section 1: «Администратор» */}
      <div className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-text-muted px-1">
          {t('settings.sectionAdmin') || 'Administrator'}
        </h2>
        <Card className="p-0 divide-y divide-white/[0.06] overflow-hidden">
          {/* Row 1: Password change */}
          <div className="p-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <div className="shrink-0 drop-shadow-[0_0_8px_rgba(212,162,76,0.25)]">
                  <AppIcon name="password" size={28} />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold text-zinc-100">{t('settings.changePassword')}</h3>
                  <p className="text-[11px] text-text-muted mt-0.5">
                    {t('auth.username')}: <span className="text-zinc-200 font-medium font-mono">{username ?? 'admin'}</span>
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0 self-start sm:self-auto">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-8 text-xs gap-1.5"
                  onClick={() => setShowPasswordForm((prev) => !prev)}
                >
                  <KeyRound size={13} />
                  <span>{t(showPasswordForm ? 'common.cancel' : 'settings.changePassword')}</span>
                  {showPasswordForm ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                </Button>
              </div>
            </div>

            {showPasswordForm && (
              <form onSubmit={onPasswordSubmit} className="pt-3 mt-3 border-t border-white/5 space-y-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <Input
                    label={t('settings.currentPassword')}
                    type="password"
                    autoComplete="current-password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    disabled={submittingPassword}
                    required
                  />
                  <Input
                    label={t('settings.newPassword')}
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    disabled={submittingPassword}
                    hint={t('settings.newPasswordHint')}
                    required
                  />
                  <Input
                    label={t('settings.confirmPassword')}
                    type="password"
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    disabled={submittingPassword}
                    error={passwordError ?? undefined}
                    required
                  />
                </div>
                {passwordSuccess && <p className="text-xs text-emerald-400">{passwordSuccess}</p>}
                <div className="flex items-center justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={submittingPassword}
                    onClick={() => {
                      setShowPasswordForm(false)
                      setPasswordError(null)
                      setCurrentPassword('')
                      setNewPassword('')
                      setConfirmPassword('')
                    }}
                  >
                    {t('common.cancel')}
                  </Button>
                  <Button type="submit" size="sm" disabled={submittingPassword}>
                    {t(submittingPassword ? 'settings.saving' : 'settings.changePassword')}
                  </Button>
                </div>
              </form>
            )}
          </div>

          {/* Row 2: 2FA */}
          <div className="p-4 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <div className="shrink-0 drop-shadow-[0_0_8px_rgba(212,162,76,0.25)]">
                  <AppIcon name="two-factor" size={28} />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-semibold text-zinc-100">{t('settings.twoFactorTitle')}</h3>
                    <Badge variant={twoFactorEnabled ? 'success' : 'default'} className="text-[10px] py-0 px-1.5">
                      {t(twoFactorEnabled ? 'settings.twoFactorEnabledBadge' : 'settings.twoFactorDisabledBadge')}
                    </Badge>
                  </div>
                  <p className="text-[11px] text-text-muted mt-0.5">{t('settings.twoFactorSubtitle')}</p>
                </div>
              </div>

              {!settingUp2fa && !disabling2fa && (
                <div className="flex items-center gap-2 shrink-0 self-start sm:self-auto">
                  {twoFactorEnabled ? (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="h-8 text-xs text-rose-400 hover:text-rose-300"
                      disabled={totpBusy}
                      onClick={() => {
                        setDisabling2fa(true)
                        setTotpError(null)
                        setTotpSuccess(null)
                      }}
                    >
                      {t('settings.twoFactorDisableBtn')}
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      disabled={totpBusy}
                      onClick={handleStartSetup2fa}
                      className="h-8 text-xs gap-1.5"
                    >
                      <KeyRound size={13} />
                      {t('settings.twoFactorEnableBtn')}
                    </Button>
                  )}
                </div>
              )}
            </div>

            {totpSuccess && <p className="text-xs text-emerald-400">{totpSuccess}</p>}
            {totpError && <p className="text-xs text-rose-400">{totpError}</p>}

            {/* QR Sub-panel */}
            {!twoFactorEnabled && settingUp2fa && totpSetup && (
              <form onSubmit={handleEnable2fa} className="mt-3 pt-3 border-t border-white/5 flex flex-col sm:flex-row items-center sm:items-start gap-4">
                <div className="p-2 bg-white rounded-lg shrink-0 flex items-center justify-center">
                  <QrImage data={totpSetup.otpauthUri} size={116} />
                </div>
                <div className="flex-1 space-y-2.5 w-full min-w-0">
                  <p className="text-xs font-medium text-zinc-300">{t('settings.twoFactorScanQr')}</p>
                  <div className="space-y-1">
                    <p className="text-[11px] text-text-muted">{t('settings.twoFactorManualKey')}</p>
                    <code className="block text-[11px] font-mono bg-black/40 border border-white/10 px-2.5 py-1 rounded-md text-amber-300 select-all break-all">
                      {totpSetup.secret}
                    </code>
                  </div>
                  <div className="space-y-1">
                    <Input
                      label={t('settings.twoFactorEnterCode')}
                      className="font-mono text-center tracking-widest text-sm"
                      autoComplete="one-time-code"
                      placeholder="123456"
                      maxLength={6}
                      value={enableCode}
                      onChange={(e) => setEnableCode(e.target.value.replace(/\D/g, ''))}
                      disabled={totpBusy}
                      required
                    />
                  </div>
                  <div className="flex items-center justify-end gap-2 pt-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={totpBusy}
                      onClick={() => {
                        setSettingUp2fa(false)
                        setEnableCode('')
                        setTotpError(null)
                      }}
                    >
                      {t('common.cancel')}
                    </Button>
                    <Button type="submit" size="sm" disabled={totpBusy || enableCode.length !== 6}>
                      {t(totpBusy ? 'settings.twoFactorActivating' : 'settings.twoFactorActivate')}
                    </Button>
                  </div>
                </div>
              </form>
            )}

            {/* Disable 2FA Form */}
            {twoFactorEnabled && disabling2fa && (
              <form onSubmit={handleDisable2fa} className="mt-3 pt-3 border-t border-white/5 space-y-2.5">
                <p className="text-[11px] text-zinc-300">{t('settings.twoFactorDisableHint')}</p>
                <div className="grid gap-3 sm:grid-cols-2">
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
                    className="font-mono text-center tracking-widest text-sm"
                    autoComplete="one-time-code"
                    placeholder="123456"
                    maxLength={6}
                    value={disableCode}
                    onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, ''))}
                    disabled={totpBusy}
                    required
                  />
                </div>
                <div className="flex items-center justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={totpBusy}
                    onClick={() => {
                      setDisabling2fa(false)
                      setDisablePassword('')
                      setDisableCode('')
                      setTotpError(null)
                    }}
                  >
                    {t('common.cancel')}
                  </Button>
                  <Button
                    type="submit"
                    variant="secondary"
                    size="sm"
                    className="text-rose-400 hover:text-rose-300"
                    disabled={totpBusy || !disablePassword || disableCode.length !== 6}
                  >
                    {t('settings.twoFactorDisableBtn')}
                  </Button>
                </div>
              </form>
            )}
          </div>
        </Card>
      </div>

      {/* Section 2: «Версия системы» */}
      <div className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-text-muted px-1">
          {t('settings.sectionVersion') || 'System Version'}
        </h2>
        <Card className="p-4 space-y-3 overflow-hidden">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="h-7 w-7 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center shrink-0">
                <Sparkles size={14} className="text-amber-400" />
              </div>
              <div className="min-w-0">
                <h3 className="font-semibold text-sm text-zinc-100">{t('settings.updateTitle')}</h3>
                <p className="text-[11px] text-text-muted">{t('settings.updateSubtitle')}</p>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              <a
                href="https://github.com/Linchevatel/Hoplyra"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 h-7 rounded-lg text-xs font-medium bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 text-zinc-300 hover:text-white transition-colors"
              >
                <span>{t('settings.githubRepo') || 'GitHub Repository'}</span>
                <ExternalLink size={12} className="text-text-muted" />
              </a>

              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={checkingUpdate || applyingUpdate}
                onClick={() => handleCheckUpdate(true)}
                className="h-7 text-xs px-2.5 gap-1.5"
              >
                <RefreshCw size={12} className={checkingUpdate ? 'animate-spin' : ''} />
                {t(checkingUpdate ? 'settings.checkingUpdate' : 'settings.checkUpdate')}
              </Button>

              {updateInfo?.hasUpdate && (
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  disabled={applyingUpdate || checkingUpdate}
                  onClick={handleApplyUpdate}
                  className="gap-1.5 h-7 text-xs px-2.5 bg-amber-500 hover:bg-amber-400 text-zinc-950 font-semibold"
                >
                  <Sparkles size={12} />
                  {t(applyingUpdate ? 'settings.applyingUpdate' : 'settings.applyUpdate')}
                </Button>
              )}
            </div>
          </div>

          {/* Version pills */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <div className="px-2.5 py-1 rounded-md bg-white/[0.03] border border-white/5 flex items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wider text-text-muted font-medium">
                {t('settings.currentVersion')}:
              </span>
              <span className="font-mono text-zinc-100 font-semibold">
                {updateInfo?.currentVersion || '...'}
              </span>
            </div>

            <div className="px-2.5 py-1 rounded-md bg-white/[0.03] border border-white/5 flex items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wider text-text-muted font-medium">
                {t('settings.latestVersion')}:
              </span>
              <span className="font-mono text-zinc-100 font-semibold">
                {updateInfo?.latestVersion || '...'}
              </span>
              {updateInfo?.hasUpdate ? (
                <Badge variant="warning" className="text-[9px] py-0 px-1 bg-amber-500/20 text-amber-300 border-amber-500/30">
                  new
                </Badge>
              ) : updateInfo ? (
                <Badge variant="success" className="text-[9px] py-0 px-1">
                  latest
                </Badge>
              ) : null}
            </div>

            {updateInfo && !updateInfo.hasUpdate && !updateError && (
              <div className="flex items-center gap-1.5 text-xs text-emerald-400/90 pl-1">
                <CircleCheck size={13} className="text-emerald-400 shrink-0" />
                <span>{t('settings.upToDate')}</span>
              </div>
            )}
          </div>

          {updateError && (
            <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-2.5 py-1.5 text-xs text-red-400">
              {updateError}
            </div>
          )}

          {updateNotice && (
            <div className="rounded-lg bg-amber-500/10 border border-amber-500/20 px-2.5 py-1.5 text-xs text-amber-300 flex items-center gap-1.5">
              <RefreshCw size={12} className="animate-spin shrink-0" />
              <span>{updateNotice}</span>
            </div>
          )}

          {/* Release notes box */}
          {updateInfo?.hasUpdate && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-amber-200">
                  {t('settings.updateAvailable', { version: updateInfo.latestVersion })}
                </p>
                {updateInfo.releaseUrl && (
                  <a
                    href={updateInfo.releaseUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300 hover:underline"
                  >
                    <span>{t('settings.viewReleaseNotes')}</span>
                    <ExternalLink size={10} />
                  </a>
                )}
              </div>
              {updateInfo.releaseNotes && (
                <div className="text-[11px] text-zinc-300 bg-black/40 rounded p-2 max-h-28 overflow-y-auto whitespace-pre-wrap font-mono border border-white/5">
                  {updateInfo.releaseNotes}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* Section 3: «Резервное копирование» */}
      <div className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-text-muted px-1">
          {t('settings.sectionBackup') || 'Backups & Recovery'}
        </h2>
        <Card className="p-4 space-y-3 overflow-hidden">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div className="shrink-0 drop-shadow-[0_0_8px_rgba(212,162,76,0.25)]">
                <AppIcon name="backup" size={28} />
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-zinc-100">{t('settings.dbBackupTitle')}</h3>
                <p className="text-[11px] text-text-muted mt-0.5">{t('settings.dbBackupHint')}</p>
                {backupDir && (
                  <p className="text-[10px] text-text-muted mt-0.5 font-mono truncate">
                    {t('settings.dbBackupDir')}: <span className="text-zinc-300">{backupDir}/</span>
                  </p>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0 self-start sm:self-auto flex-wrap">
              <Button
                type="button"
                size="sm"
                disabled={backingUp || restoreBusy}
                onClick={handleDbBackup}
                className="h-8 text-xs"
              >
                {t(backingUp ? 'settings.dbBackupCreating' : 'settings.dbBackup')}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-8 text-xs"
                disabled={restoreBusy || backingUp}
                onClick={() => uploadRef.current?.click()}
              >
                <Upload size={13} className="mr-1.5" />
                {t(uploadRestoring ? 'settings.dbRestoring' : 'settings.dbRestoreUpload')}
              </Button>
            </div>
          </div>

          {backupError && <p className="text-xs text-rose-400">{backupError}</p>}
          {backupPath && (
            <p className="text-[11px] text-emerald-400 font-mono truncate">
              {t('settings.dbBackupSuccess')}: {backupPath}
            </p>
          )}

          <div className="space-y-1.5 pt-1">
            <p className="font-medium text-xs text-zinc-300">{t('settings.dbRestoreTitle')}</p>
            {backups.length === 0 ? (
              <div className="rounded-lg border border-dashed border-white/10 py-2.5 px-3 text-center text-text-muted text-xs">
                {t('settings.dbRestoreEmpty')}
              </div>
            ) : (
              <ul className="max-h-36 overflow-y-auto divide-y divide-white/5 rounded-lg border border-white/5 bg-black/20 pr-1">
                {backups.map((item) => (
                  <li
                    key={item.name}
                    className="px-3 py-1.5 flex items-center justify-between gap-2 hover:bg-white/[0.02]"
                  >
                    <div className="min-w-0">
                      <p className="text-[11px] font-mono text-zinc-300 truncate">{item.name}</p>
                      <p className="text-[10px] text-text-muted">
                        {formatBackupDate(item.createdAt)} · {formatBytes(item.sizeBytes)}
                      </p>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      className="h-6 px-2 text-[11px] shrink-0"
                      disabled={restoreBusy || backingUp}
                      onClick={() => handleRestore(item.name)}
                    >
                      <RotateCcw size={11} className="mr-1" />
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
          </div>
        </Card>
      </div>
    </div>
  )
}
