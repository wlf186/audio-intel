import {defineConfig} from '@playwright/test'
import {existsSync} from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'

export default defineConfig({
 testDir:'./e2e',
 timeout:30_000,
 retries:0,
 reporter:'line',
 outputDir:path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../logs/playwright'),
 use:{
  baseURL:process.env.AUDIO_INTEL_TEST_BASE_URL||'http://127.0.0.1:20810',
  browserName:'chromium',
  headless:true,
  viewport:{width:1440,height:900},
  locale:'zh-CN',
  screenshot:'only-on-failure',
  trace:'retain-on-failure',
  launchOptions:process.platform==='linux'?{executablePath:existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined,args:['--no-sandbox']}:undefined,
 },
})
