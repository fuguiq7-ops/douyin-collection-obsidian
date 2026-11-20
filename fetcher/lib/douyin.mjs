import { CaptureSink, autoScroll } from './capture.mjs';
import { ensureDir, log as defaultLog, sleep } from './util.mjs';

export const DOUYIN_HOME = 'https://www.douyin.com/';

export const COLLECTION_TAB_URLS = [
  'https://www.douyin.com/user/self?showTab=favorite_collection',
  'https://www.douyin.com/user/self?showTab=collection',
  'https://www.douyin.com/user/self',
];
// 收藏夹子标签页（含收藏夹卡片列表）
const FOLDER_TAB_URL = 'https://www.douyin.com/user/self?showTab=favorite_collection&showSubTab=favorite_folder';

export const PATTERNS = {
  listcollection: /\/aweme\/v1\/web\/aweme\/listcollection\//,
  collectsList: /\/aweme\/v1\/web\/collects\/list\//,
  collectsVideo: /\/aweme\/v1\/web\/collects\/video\/list\//,
  watchlater: /\/aweme\/v1\/web\/watchlater\/list\//,
};

export async function isLoggedIn(page) {
  const client = await page.createCDPSession();
  try {
    const { cookies } = await client.send('Network.getAllCookies');
    return cookies.some((c) => c.name === 'sessionid' && c.value);
  } finally {
    try {
      await client.detach();
    } catch {
      // ignore
    }
  }
}

export async function clickByText(page, text) {
  return page
    .evaluate((needle) => {
      const all = [...document.querySelectorAll('a, [role="tab"], button, [data-e2e], span, div')];
      const matchText = (own) => own === needle || (own.startsWith(needle) && own.length <= needle.length + 10);
      const candidates = all.filter((el) => {
        const own = (el.textContent || '').trim();
        if (!matchText(own)) return false;
        if (el.children.length === 0) return true;
        return el.hasAttribute('data-e2e') || el.getAttribute('role') === 'tab';
      });
      if (!candidates.length) return false;
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top >= 0 && r.top < 800;
      };
      const el = candidates.find(visible) || candidates[0];
      el.scrollIntoView({ block: 'center' });
      el.click();
      return true;
    }, text)
    .catch(() => false);
}

// 在收藏夹卡片网格内按名称精确匹配卡片，用真实鼠标坐标点击。
// 不能用全局文本模糊匹配：顶部导航「AI抖音」等条目也会以前缀命中，导致点错入口。
async function clickFolderCard(page, name) {
  const located = await page
    .evaluate((needle) => {
      const lists = document.querySelectorAll('[data-e2e=scroll-list], ul.QhXy7t32');
      for (const ul of lists) {
        for (const li of ul.querySelectorAll('li')) {
          const p = li.querySelector('p');
          if (!p || (p.textContent || '').trim() !== needle) continue;
          const r = li.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0) continue;
          li.scrollIntoView({ block: 'center' });
          const r2 = li.getBoundingClientRect();
          return { ok: true, x: r2.x + r2.width / 2, y: r2.y + r2.height / 2 };
        }
      }
      return { ok: false };
    }, name)
    .catch(() => ({ ok: false }));
  if (!located.ok) return false;
  await page.mouse.click(located.x, located.y).catch(() => {});
  return true;
}

// 点击收藏夹后，等待作品视图真正打开（出现返回按钮或作品链接），再开始滚动翻页
async function waitForFolderView(page, maxSeconds) {
  const deadline = Date.now() + Math.max(1, Number(maxSeconds) || 1) * 1000;
  while (Date.now() < deadline) {
    const ready = await page
      .evaluate(() => {
        const back = [...document.querySelectorAll('div,span,button,a')].some((e) => (e.textContent || '').trim() === '返回');
        const grid = document.querySelector('a[href*="/video/"]');
        return back || Boolean(grid);
      })
      .catch(() => false);
    if (ready) return true;
    await sleep(500);
  }
  return false;
}

async function waitForCapture(sink, maxSeconds) {
  const deadline = Date.now() + Math.max(1, Number(maxSeconds) || 1) * 1000;
  while (Date.now() < deadline) {
    if (sink.captures.length) return true;
    await sleep(500);
  }
  return sink.captures.length > 0;
}

function captureBelongsToFolder(capture, folderId) {
  try {
    return new URL(capture.url).searchParams.get('collects_id') === String(folderId);
  } catch {
    return false;
  }
}

// 点击收藏夹后，等待该收藏夹自己的作品接口响应，确认视图已切换，再滚动翻页
// 第 1 页可能是进入页面前预加载的（已在 captures 中），存在即算就绪；否则等待新增响应
async function waitForFolderCapture(sink, folderId, maxSeconds, baseline) {
  if (sink.captures.some((c) => captureBelongsToFolder(c, folderId))) return true;
  const deadline = Date.now() + Math.max(1, Number(maxSeconds) || 1) * 1000;
  while (Date.now() < deadline) {
    const fresh = sink.captures.slice(baseline);
    if (fresh.some((c) => captureBelongsToFolder(c, folderId))) return true;
    await sleep(500);
  }
  return false;
}

export function collectFolderCandidates(json) {
  const found = [];
  const walk = (node, depth) => {
    if (!node || typeof node !== 'object' || depth > 8) return;
    if (Array.isArray(node)) {
      for (const item of node) walk(item, depth + 1);
      return;
    }
    const keys = Object.keys(node);
    // 优先取未丢精度的字符串 id（collects_id_str），避免大整数被 Number 化丢精度
    const idStrKey = keys.find((k) => /^(collects?_?id_str|collect_id_str|folder_id_str)$/i.test(k));
    const idKey = idStrKey || keys.find((k) => /^(collects?_?id|collect_id|folder_id)$/i.test(k));
    const nameKey = keys.find((k) => /(collects?_?name|collect_name|folder_name)$/i.test(k));
    if (idKey && nameKey && typeof node[nameKey] === 'string' && node[nameKey].trim()) {
      found.push({ id: String(node[idKey]), name: String(node[nameKey]).trim() });
    }
    for (const value of Object.values(node)) {
      if (value && typeof value === 'object') walk(value, depth + 1);
    }
  };
  walk(json, 0);
  const seen = new Set();
  return found.filter((f) => {
    const key = `${f.id}|${f.name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export async function probeWatchLater(pages, endpointPaths) {
  const foundInPages = [];
  for (const page of pages) {
    try {
      const text = await page.evaluate(() => (document.body ? document.body.innerText : ''));
      if (text && text.includes('稍后再看')) foundInPages.push(page.url());
    } catch {
      // ignore
    }
  }
  const candidateEndpoints = [...endpointPaths].filter((p) => /watch|later|history/i.test(p));
  return {
    found_in_pages: foundInPages,
    candidate_endpoints: candidateEndpoints,
    verdict: foundInPages.length || candidateEndpoints.length ? 'found' : 'not_found',
  };
}

export async function captureDouyin({ browser, cfg, outDir, log = defaultLog }) {
  const startedAt = new Date().toISOString();
  const errors = [];
  const endpointPaths = new Set();
  ensureDir(outDir);

  const delay = cfg.capture;
  const sinks = {
    listcollection: new CaptureSink({ outDir, matcher: PATTERNS.listcollection, kind: 'listcollection', log }),
    collectsList: new CaptureSink({ outDir, matcher: PATTERNS.collectsList, kind: 'collects-list', log }),
    collectsVideo: new CaptureSink({
      outDir,
      matcher: PATTERNS.collectsVideo,
      kind: (url) => {
        let id = 'unknown';
        try {
          id = new URL(url).searchParams.get('collects_id') || 'unknown';
        } catch {
          // ignore
        }
        return `collects-video.${id}`;
      },
      log,
    }),
    watchlater: new CaptureSink({ outDir, matcher: PATTERNS.watchlater, kind: 'watchlater', log }),
  };

  const seenPages = new Set();
  const allPages = [];
  const attachPage = (page) => {
    if (!page || seenPages.has(page)) return;
    seenPages.add(page);
    allPages.push(page);
    for (const sink of Object.values(sinks)) sink.attach(page);
    page.on('response', (res) => {
      try {
        endpointPaths.add(new URL(res.url()).pathname);
      } catch {
        // ignore
      }
    });
    page.on('dialog', (dialog) => {
      void dialog.dismiss().catch(() => {});
    });
  };
  for (const p of await browser.pages()) attachPage(p);
  browser.on('targetcreated', (target) => {
    if (target.type() === 'page') {
      void target
        .page()
        .then((p) => attachPage(p))
        .catch(() => {});
    }
  });

  const page = await browser.newPage();
  await page.bringToFront().catch(() => {});
  const timeout = Math.max(15, Number(delay.page_load_timeout_sec) || 60) * 1000;

  log('打开抖音首页，检查登录状态 ...');
  await page
    .goto(DOUYIN_HOME, { waitUntil: 'domcontentloaded', timeout })
    .catch((e) => errors.push(`打开首页失败: ${e.message}`));
  await sleep(3000);
  if (!(await isLoggedIn(page))) {
    return {
      started_at: startedAt,
      ended_at: new Date().toISOString(),
      login_required: true,
      collection: { url: null, pages: 0, exhausted: false, reason: null },
      collects_list: { pages: 0 },
      folders: { discovered: [], results: [], complete: false },
      favorites_complete: false,
      watch_later: { verdict: 'unknown', found_in_pages: [], candidate_endpoints: [] },
      endpoint_paths: [...endpointPaths],
      sink_errors: [],
      errors: [...errors, '未登录：需要在专用 Edge 窗口里扫码登录抖音'],
      partial: true,
    };
  }

  const syncCfg = cfg.sync || { include_favorites: true, include_watch_later: true, include_folders: [] };
  const includeFavorites = syncCfg.include_favorites !== false;
  const includeWatchLater = syncCfg.include_watch_later !== false;
  const folderWhitelist = Array.isArray(syncCfg.include_folders) ? syncCfg.include_folders.map(String) : [];
  const folderMatch = (folder) => {
    if (!folderWhitelist.length) return true;
    return folderWhitelist.some((w) => w && (w === folder.name || w === folder.id));
  };

  let collectionUrl = null;
  let favoritesScroll = null;
  if (includeFavorites) {
    log('登录正常，打开「收藏」列表 ...');
    for (let attempt = 1; attempt <= 2 && !collectionUrl; attempt++) {
      for (const url of COLLECTION_TAB_URLS) {
        if (sinks.listcollection.captures.length) {
          collectionUrl = page.url();
          break;
        }
        try {
          await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
        } catch (e) {
          errors.push(`打开 ${url} 失败: ${e.message}`);
          continue;
        }
        // 冷启动时数据可能十几秒才返回：轮询等待，不再用固定 3.5 秒就翻页
        if (!(await waitForCapture(sinks.listcollection, 30))) {
          const clicked = await clickByText(page, '收藏');
          if (clicked) await waitForCapture(sinks.listcollection, 15);
        }
        if (sinks.listcollection.captures.length) {
          collectionUrl = page.url();
          break;
        }
      }
      if (!collectionUrl && attempt < 2) {
        log('  ⚠ 本次未捕获到收藏数据，30 秒后重试一轮 ...');
        await sleep(30000);
      }
    }

    if (collectionUrl) {
      log('开始滚动抓取全量收藏 ...');
      favoritesScroll = await autoScroll(page, {
        sink: sinks.listcollection,
        delayMin: delay.delay_min_sec,
        delayMax: delay.delay_max_sec,
        maxIdle: delay.max_idle_scrolls,
        maxScrolls: delay.max_scrolls,
        log,
      });
    } else {
      errors.push('未能打开「收藏」列表页（可能是页面结构变化）。');
    }
  } else {
    log('配置已关闭「全部收藏」同步，跳过收藏列表抓取。');
    // 仍需进入收藏页才能加载收藏夹列表接口
    try {
      await page.goto(COLLECTION_TAB_URLS[0], { waitUntil: 'domcontentloaded', timeout });
      await sleep(3000);
    } catch (e) {
      errors.push(`打开收藏页失败: ${e.message}`);
    }
  }

  if (!sinks.collectsList.captures.length) {
    const clicked = await clickByText(page, '收藏夹');
    if (clicked) {
      await waitForCapture(sinks.collectsList, 20);
      await sleep(2000);
    }
  }
  const folders = [];
  for (const cap of sinks.collectsList.captures) {
    for (const folder of collectFolderCandidates(cap.json)) {
      if (!folders.some((x) => x.id === folder.id)) folders.push(folder);
    }
  }
  if (folders.length) {
    log(`发现 ${folders.length} 个收藏夹：${folders.map((f) => f.name).join('、')}`);
  } else {
    log('未捕获到收藏夹列表（可能没有收藏夹，或页面结构变化）。');
  }
  const selectedFolders = folderWhitelist.length ? folders.filter(folderMatch) : folders;
  if (folderWhitelist.length) {
    log(`白名单收藏夹 ${selectedFolders.length}/${folders.length} 个：${selectedFolders.map((f) => f.name).join('、') || '（无匹配，请检查 include_folders 配置）'}`);
  }

  const folderResults = [];
  for (const folder of selectedFolders) {
    const before = sinks.collectsVideo.captures.length;
    try {
      await page.goto(FOLDER_TAB_URL, { waitUntil: 'domcontentloaded', timeout });
      await sleep(3000);
      // 优先在收藏夹卡片网格内精确定位点击；回退到通用文本点击
      let clicked = await clickFolderCard(page, folder.name);
      if (!clicked) clicked = await clickByText(page, folder.name);
      if (!clicked) {
        folderResults.push({ id: folder.id, name: folder.name, ok: false, reason: 'click-failed', pages: 0, exhausted: false });
        continue;
      }
      // 等待作品视图真正打开，再滚动翻页
      await waitForFolderView(page, 15);
      await sleep(1500);
      const folderReady = await waitForFolderCapture(sinks.collectsVideo, folder.id, 20, before);
      if (!folderReady) {
        log(`  ⚠ 未捕获到收藏夹「${folder.name}」的作品接口，尝试继续滚动。`);
      }
      log(`抓取收藏夹「${folder.name}」...`);
      const scroll = await autoScroll(page, {
        sink: sinks.collectsVideo,
        delayMin: delay.delay_min_sec,
        delayMax: delay.delay_max_sec,
        maxIdle: delay.max_idle_scrolls,
        maxScrolls: delay.max_scrolls,
        log,
      });
      const pages = sinks.collectsVideo.captures.length - before;
      if (!pages) errors.push(`收藏夹「${folder.name}」没有捕获到作品列表。`);
      folderResults.push({
        id: folder.id,
        name: folder.name,
        ok: pages > 0,
        reason: pages > 0 ? scroll.reason : 'no-capture',
        pages,
        exhausted: scroll.exhausted,
      });
    } catch (e) {
      errors.push(`收藏夹「${folder.name}」抓取失败: ${e.message}`);
      folderResults.push({ id: folder.id, name: folder.name, ok: false, reason: e.message, pages: 0, exhausted: false });
    }
  }

  const watchLater = await probeWatchLater(allPages, endpointPaths);
  const favoritesComplete = Boolean(favoritesScroll?.exhausted);
  const foldersComplete =
    folders.length > 0 ? folderResults.every((f) => f.ok && f.exhausted) : sinks.collectsList.captures.length > 0;

  let watchLaterScroll = null;
  if (includeWatchLater) {
    log('打开「稍后再看」列表 ...');
    for (let attempt = 1; attempt <= 2 && !sinks.watchlater.captures.length; attempt++) {
      try {
        await page.goto('https://www.douyin.com/user/self?showTab=watch_later', { waitUntil: 'domcontentloaded', timeout });
        if (!(await waitForCapture(sinks.watchlater, 30))) {
          const clicked = await clickByText(page, '稍后再看');
          if (clicked) await waitForCapture(sinks.watchlater, 15);
        }
        if (sinks.watchlater.captures.length) {
          watchLaterScroll = await autoScroll(page, {
            sink: sinks.watchlater,
            delayMin: delay.delay_min_sec,
            delayMax: delay.delay_max_sec,
            maxIdle: delay.max_idle_scrolls,
            maxScrolls: delay.max_scrolls,
            log,
          });
        } else if (attempt < 2) {
          log('  ⚠ 本次未捕获到「稍后再看」数据，30 秒后重试一轮 ...');
          await sleep(30000);
        } else {
          errors.push('未能捕获「稍后再看」列表数据（可能页面结构变化）。');
        }
      } catch (e) {
        errors.push(`抓取「稍后再看」失败: ${e.message}`);
      }
    }
  } else {
    log('配置已关闭「稍后再看」同步，跳过该列表。');
  }
  let watchLaterStats = { unique: 0, listNum: 0, invalid: 0 };
  if (includeWatchLater && sinks.watchlater.captures.length) {
    const uniq = new Set();
    const invalids = new Set();
    let listNum = 0;
    for (const cap of sinks.watchlater.captures) {
      const data = cap.json || {};
      const list = Array.isArray(data.items) ? data.items : Array.isArray(data.aweme_list) ? data.aweme_list : [];
      for (const it of list) {
        if (it && it.aweme_id) uniq.add(String(it.aweme_id));
      }
      for (const id of data.invalid_item_ids || []) invalids.add(String(id));
      if (typeof data.list_num === 'number') listNum = Math.max(listNum, data.list_num);
    }
    watchLaterStats = { unique: uniq.size, listNum, invalid: invalids.size };
  }
  const watchLaterComplete =
    Boolean(watchLaterScroll?.exhausted) ||
    (watchLaterStats.listNum > 0 && watchLaterStats.unique + watchLaterStats.invalid >= watchLaterStats.listNum);
  if (watchLaterStats.listNum > 0 && !watchLaterComplete) {
    errors.push(
      `「稍后再看」可能未抓全：捕获 ${watchLaterStats.unique} 条 / 标记总数 ${watchLaterStats.listNum}（含失效 ${watchLaterStats.invalid}）`,
    );
  }

  return {
    started_at: startedAt,
    ended_at: new Date().toISOString(),
    login_required: false,
    collection: {
      url: collectionUrl,
      pages: sinks.listcollection.captures.length,
      exhausted: Boolean(favoritesScroll?.exhausted),
      reason: favoritesScroll?.reason ?? null,
    },
    collects_list: { pages: sinks.collectsList.captures.length },
    folders: { discovered: folders, results: folderResults, complete: foldersComplete },
    favorites_complete: favoritesComplete,
    watch_later: {
      captured: sinks.watchlater.captures.length > 0,
      pages: sinks.watchlater.captures.length,
      exhausted: Boolean(watchLaterScroll?.exhausted),
      complete: watchLaterComplete,
      unique_items: watchLaterStats.unique,
      list_num: watchLaterStats.listNum,
      invalid_items: watchLaterStats.invalid,
      probe: watchLater,
    },
    endpoint_paths: [...endpointPaths],
    sink_errors: Object.values(sinks).flatMap((s) => s.errors),
    body_read_failures: Object.values(sinks).reduce((sum, s) => sum + s.bodyErrors, 0),
    errors,
    partial: !(favoritesComplete && foldersComplete),
  };
}
