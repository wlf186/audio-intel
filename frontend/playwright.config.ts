import {defineConfig} from '@playwright/test'

export default defineConfig({
 testDir:'./e2e',
 timeout:30_000,
 retries:0,
 reporter:'line',
 outputDir:'/tmp/audio-intel-playwright',
 use:{
  baseURL:'http://127.0.0.1:20810',
  browserName:'chromium',
  headless:true,
  viewport:{width:1440,height:900},
  launchOptions:{executablePath:'/usr/bin/chromium',args:['--no-sandbox']},
 },
})
