import assert from 'node:assert/strict'
import test from 'node:test'
import {
  effectiveAsrForSource,
  failureToYoutubePrompt,
  hasYoutubeCookiesConfigured,
  resolveYoutubeCookiePermissionKind,
  resolveYoutubeSubtitlePrompt,
} from '../src/youtubeGuidance.ts'

const YT = 'https://www.youtube.com/watch?v=1-Fo8McCD9w'
const BILI = 'https://www.bilibili.com/video/BV1rxqmBhE91?p=1'

test('hasYoutubeCookiesConfigured validates browser and file modes explicitly', () => {
  assert.equal(hasYoutubeCookiesConfigured(undefined), false)
  assert.equal(hasYoutubeCookiesConfigured({ mode: 'none', browser: 'chrome', filePath: '' }), false)
  assert.equal(hasYoutubeCookiesConfigured({ mode: 'browser', browser: 'chrome', filePath: '' }), true)
  assert.equal(hasYoutubeCookiesConfigured({ mode: 'file', browser: 'chrome', filePath: '' }), false)
  assert.equal(hasYoutubeCookiesConfigured({ mode: 'file', browser: 'chrome', filePath: '/tmp/cookies.txt' }), true)
})

test('YouTube without cookies never runs or suggests ASR before configuring cookies', () => {
  const asrOn = { enabled: true, model: 'paraformer-v2' }
  assert.equal(effectiveAsrForSource(asrOn, true, false).enabled, false)
  assert.equal(effectiveAsrForSource(asrOn, true, true).enabled, true)
  assert.equal(effectiveAsrForSource(asrOn, false, false).enabled, true)

  for (const input of [
    { code: 'youtube_verification_required' },
    { code: 'subtitles_unavailable', status: 'needs_asr' },
    { code: 'youtube_subtitles_unconfirmed' },
    { code: 'youtube_subtitle_invalid' },
  ]) {
    const prompt = resolveYoutubeSubtitlePrompt({
      source: YT,
      isYoutube: true,
      cookiesConfigured: false,
      asrEnabled: false,
      ...input,
    })
    assert.equal(prompt?.kind, 'configure_cookies')
  }
})

test('YouTube with cookies configured checks cookie failures first, then offers ASR only when subtitles are absent', () => {
  for (const code of [
    'youtube_verification_required',
    'youtube_cookies_unavailable',
    'youtube_cookies_safari_permission',
    'youtube_cookies_permission_denied',
    'youtube_cookies_keychain_denied',
    'youtube_cookies_not_found',
    'youtube_cookies_format_invalid',
    'youtube_cookies_empty',
    'youtube_cookies_expired',
  ]) {
    const prompt = failureToYoutubePrompt(YT, true, true, false, {
      code,
      message: 'Cookie failed',
      retryable: false,
      action: 'settings',
    })
    assert.equal(prompt?.kind, 'fix_cookies')
  }

  const offer = resolveYoutubeSubtitlePrompt({
    source: YT,
    isYoutube: true,
    cookiesConfigured: true,
    asrEnabled: false,
    code: 'subtitles_unavailable',
    status: 'needs_asr',
  })
  assert.equal(offer?.kind, 'offer_asr')

  // When ASR is already enabled and cookies are configured, no extra modal is needed.
  const noModal = resolveYoutubeSubtitlePrompt({
    source: YT,
    isYoutube: true,
    cookiesConfigured: true,
    asrEnabled: true,
    code: 'subtitles_unavailable',
    status: 'needs_asr',
  })
  assert.equal(noModal, undefined)
})

test('non-YouTube sources are unaffected by YouTube subtitle prompts', () => {
  assert.equal(resolveYoutubeSubtitlePrompt({
    source: BILI,
    isYoutube: false,
    cookiesConfigured: false,
    asrEnabled: false,
    code: 'subtitles_unavailable',
    status: 'needs_asr',
  }), undefined)
})

test('resolveYoutubeCookiePermissionKind classifies macOS disk, Safari, and Keychain permission errors', () => {
  assert.equal(resolveYoutubeCookiePermissionKind('youtube_cookies_permission_denied'), 'full_disk_access')
  assert.equal(resolveYoutubeCookiePermissionKind('youtube_cookies_safari_permission'), 'safari_full_disk_access')
  assert.equal(resolveYoutubeCookiePermissionKind('youtube_cookies_keychain_denied'), 'keychain')
  assert.equal(resolveYoutubeCookiePermissionKind('youtube_cookies_empty'), null)
  assert.equal(resolveYoutubeCookiePermissionKind('youtube_cookies_not_found'), null)
  assert.equal(resolveYoutubeCookiePermissionKind('youtube_cookies_expired'), null)
  assert.equal(resolveYoutubeCookiePermissionKind(null), null)
  assert.equal(resolveYoutubeCookiePermissionKind(undefined), null)
})
