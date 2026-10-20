import {test} from './fixtures'
import {scrollbarCases} from './scrollbar-fixture'

test.use({launchOptions:{ignoreDefaultArgs:['--hide-scrollbars'],args:[...(process.platform==='linux'?['--no-sandbox']:[]),'--disable-features=OverlayScrollbar,OverlayScrollbars']}})
scrollbarCases(true)
