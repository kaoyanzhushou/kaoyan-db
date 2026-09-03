// 考研资料库 Service Worker
// 版本号：更新此值可强制清除旧缓存
const CACHE_VERSION = 'kaoyan-db-v1';
const CACHE_NAME = `kaoyan-db-${CACHE_VERSION}`;

// 预缓存的核心资源
const PRECACHE_URLS = [
  './',
  './index.html',
];

// 安装阶段：预缓存核心资源
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
      .catch((err) => console.log('[SW] 预缓存失败:', err))
  );
});

// 激活阶段：清除旧版本缓存
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name.startsWith('kaoyan-db-') && name !== CACHE_NAME)
            .map((name) => caches.delete(name))
        );
      })
      .then(() => self.clients.claim())
  );
});

// 请求拦截：缓存优先策略
self.addEventListener('fetch', (event) => {
  // 只处理GET请求
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // 不缓存跨域请求（如GitHub API、外部CDN）
  if (url.origin !== self.location.origin) return;

  // 不缓存API请求
  if (url.pathname.includes('/api/')) return;

  event.respondWith(
    caches.match(event.request)
      .then((cached) => {
        if (cached) {
          // 缓存命中：返回缓存，同时后台更新缓存（stale-while-revalidate）
          fetch(event.request)
            .then((response) => {
              if (response && response.status === 200) {
                const responseClone = response.clone();
                caches.open(CACHE_NAME)
                  .then((cache) => cache.put(event.request, responseClone));
              }
            })
            .catch(() => {});
          return cached;
        }

        // 缓存未命中：网络请求，成功后缓存
        return fetch(event.request)
          .then((response) => {
            if (!response || response.status !== 200 || response.type !== 'basic') {
              return response;
            }
            const responseClone = response.clone();
            caches.open(CACHE_NAME)
              .then((cache) => cache.put(event.request, responseClone));
            return response;
          })
          .catch(() => {
            // 网络失败：返回离线页面
            if (event.request.mode === 'navigate') {
              return caches.match('./index.html');
            }
            return new Response('离线状态，请检查网络连接', {
              status: 503,
              statusText: 'Service Unavailable'
            });
          });
      })
  );
});

// 接收来自页面的消息
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data === 'CLEAR_CACHE') {
    caches.keys().then((names) => {
      names.forEach((name) => caches.delete(name));
    });
  }
});
