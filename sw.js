// 考研资料库 Service Worker
// 版本号：更新此值可强制清除旧缓存
const CACHE_VERSION = 'kaoyan-db-v2';
const CACHE_NAME = `kaoyan-db-${CACHE_VERSION}`;

// 安装阶段：直接跳过等待，不做预缓存（避免大文件预缓存卡住）
self.addEventListener('install', (event) => {
  console.log('[SW] 安装中，版本:', CACHE_VERSION);
  event.waitUntil(self.skipWaiting());
});

// 激活阶段：清除旧版本缓存
self.addEventListener('activate', (event) => {
  console.log('[SW] 激活中');
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name.startsWith('kaoyan-db-') && name !== CACHE_NAME)
            .map((name) => {
              console.log('[SW] 清除旧缓存:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => self.clients.claim())
  );
});

// 请求拦截：网络优先，失败时回退到缓存
self.addEventListener('fetch', (event) => {
  // 只处理GET请求
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // 不缓存跨域请求
  if (url.origin !== self.location.origin) return;

  // 不缓存API请求
  if (url.pathname.includes('/api/')) return;

  // 不缓存sw.js本身
  if (url.pathname.endsWith('/sw.js')) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // 只缓存成功的响应
        if (response && response.status === 200 && response.type === 'basic') {
          const responseClone = response.clone();
          caches.open(CACHE_NAME)
            .then((cache) => cache.put(event.request, responseClone))
            .catch(() => {});
        }
        return response;
      })
      .catch(() => {
        // 网络失败：尝试从缓存读取
        return caches.match(event.request)
          .then((cached) => {
            if (cached) return cached;
            // 导航请求失败时返回缓存的首页
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
