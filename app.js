// 考研资料库 - 全栈版前端逻辑
const SUPABASE_URL = 'https://ifeslngcdledzgwltcbl.supabase.co';
const SUPABASE_ANON_KEY = 'sb_publishable_8PV65BZfwPABIMYw8zJMSg_y0q6pQrL';

const supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { persistSession: true, autoRefreshToken: true }
});

let currentUser = null;
let currentTab = 'dashboard';
let currentSubject = null;
let currentChapter = null;
let flashcardIndex = 0;
let flashcardFlipped = false;
let allFlashcards = [];
let dataCache = {};

// ============================================
// 数据缓存工具
// ============================================
function getCache(key, maxAge = 5 * 60 * 1000) {
    try {
        const cached = localStorage.getItem('cache_' + key);
        if (!cached) return null;
        const { data, timestamp } = JSON.parse(cached);
        if (Date.now() - timestamp > maxAge) return null;
        return data;
    } catch (e) { return null; }
}

function setCache(key, data) {
    try {
        localStorage.setItem('cache_' + key, JSON.stringify({ data, timestamp: Date.now() }));
    } catch (e) {}
}

async function fetchWithCache(table, queryFn, cacheKey, maxAge = 5 * 60 * 1000) {
    const cached = getCache(cacheKey, maxAge);
    if (cached) return cached;
    try {
        const result = await queryFn();
        if (result.data) setCache(cacheKey, result.data);
        return result.data;
    } catch (e) {
        console.error('Fetch error:', e);
        return cached || [];
    }
}

// ============================================
// 初始化（极速模式：本地缓存优先）
// ============================================
async function init() {
    updateLoading('正在加载...', 10);
    
    // 极速模式：检查localStorage中是否有supabase session缓存
    // supabase-js会把session存在 localStorage 的 sb-<project-ref>-auth-token 中
    const projectRef = SUPABASE_URL.split('//')[1].split('.')[0];
    const sessionKey = `sb-${projectRef}-auth-token`;
    const cachedSession = localStorage.getItem(sessionKey);
    
    updateLoading('检查登录状态...', 30);
    
    if (cachedSession) {
        // 有本地缓存，直接进入主应用（后台再验证）
        try {
            const sessionData = JSON.parse(cachedSession);
            if (sessionData && sessionData.access_token) {
                currentUser = {
                    id: sessionData.user?.id,
                    email: sessionData.user?.email,
                    user_metadata: sessionData.user?.user_metadata || {}
                };
                
                updateLoading('加载用户信息...', 60);
                
                // 后台验证session有效性（不阻塞UI）
                supabase.auth.getSession().then(({ data }) => {
                    if (data.session) {
                        currentUser = data.session.user;
                        document.getElementById('userName').textContent = currentUser.user_metadata?.username || currentUser.email;
                    }
                }).catch(() => {});
                
                // 加载用户profile（后台，不阻塞）
                loadUserProfile().catch(() => {});
                
                showMainApp();
                updateLoading('加载完成', 100);
                setTimeout(() => {
                    document.getElementById('loadingScreen').style.display = 'none';
                }, 200);
                return;
            }
        } catch (e) {
            console.error('Parse cached session error:', e);
        }
    }
    
    // 没有本地缓存，检查session（带超时）
    const timeoutPromise = new Promise((resolve) => {
        setTimeout(() => resolve(null), 5000);
    });
    
    const sessionPromise = supabase.auth.getSession().catch(() => ({ data: { session: null } }));
    
    const result = await Promise.race([sessionPromise, timeoutPromise]);
    const session = result?.data?.session;
    
    updateLoading('加载完成', 80);
    
    if (session) {
        currentUser = session.user;
        try { await loadUserProfile(); } catch (e) {}
        showMainApp();
    } else {
        showLoginPage();
    }
    
    setTimeout(() => {
        document.getElementById('loadingScreen').style.display = 'none';
    }, 200);
}

function showLoadingError() {
    const loadingScreen = document.getElementById('loadingScreen');
    loadingScreen.innerHTML = `
        <div class="loading-content">
            <div class="loading-icon">⚠️</div>
            <h1>连接较慢</h1>
            <p style="margin-bottom: 16px;">服务器响应超时，请检查网络后重试</p>
            <button onclick="location.reload()" style="padding: 10px 24px; background: #667eea; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px;">🔄 重新加载</button>
            <p style="margin-top: 16px; font-size: 12px; color: #9ca3af;">提示：Supabase服务器在国外，国内访问可能较慢</p>
        </div>
    `;
    loadingScreen.style.display = 'flex';
}

function updateLoading(text, progress) {
    const loadingText = document.getElementById('loadingText');
    const loadingProgress = document.getElementById('loadingProgress');
    if (loadingText) loadingText.textContent = text;
    if (loadingProgress) loadingProgress.style.width = progress + '%';
}

// ============================================
// 认证
// ============================================
function showLoginPage() {
    document.getElementById('loginPage').style.display = 'block';
    document.getElementById('mainApp').style.display = 'none';
}

function showMainApp() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('mainApp').style.display = 'block';
    document.getElementById('userName').textContent = currentUser.user_metadata?.username || currentUser.email;
    loadDashboard();
}

function switchLoginTab(tab) {
    document.querySelectorAll('.login-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('loginForm').style.display = tab === 'login' ? 'block' : 'none';
    document.getElementById('registerForm').style.display = tab === 'register' ? 'block' : 'none';
}

async function handleLogin() {
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    
    if (!email || !password) { showToast('请输入邮箱和密码'); return; }
    
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) { showToast('登录失败: ' + error.message); return; }
    
    currentUser = (await supabase.auth.getSession()).data.session.user;
    await loadUserProfile();
    showMainApp();
    showToast('登录成功！');
}

async function handleRegister() {
    const username = document.getElementById('registerUsername').value;
    const email = document.getElementById('registerEmail').value;
    const password = document.getElementById('registerPassword').value;
    const inviteCode = document.getElementById('registerInviteCode').value;
    
    if (!username || !email || !password || !inviteCode) { showToast('请填写所有字段'); return; }
    
    // 验证邀请码
    const { data: codeData, error: codeError } = await supabase
        .from('invite_codes')
        .select('*')
        .eq('code', inviteCode)
        .eq('is_used', false)
        .single();
    
    if (codeError || !codeData) { showToast('邀请码无效或已使用'); return; }
    
    // 注册
    const { error } = await supabase.auth.signUp({
        email, password,
        options: { data: { username, role: 'user' } }
    });
    
    if (error) { showToast('注册失败: ' + error.message); return; }
    
    // 标记邀请码已使用
    await supabase.from('invite_codes').update({ is_used: true, used_by: email, used_at: new Date().toISOString() }).eq('id', codeData.id);
    
    showToast('注册成功！请登录');
    switchLoginTab('login');
}

async function handleLogout() {
    await supabase.auth.signOut();
    currentUser = null;
    showLoginPage();
    showToast('已退出登录');
}

async function loadUserProfile() {
    if (!currentUser || !currentUser.id) return;
    try {
        const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000));
        const request = supabase.from('profiles').select('*').eq('id', currentUser.id).single();
        const { data } = await Promise.race([request, timeout]);
        if (data && data.role === 'admin') {
            const adminItem = document.getElementById('adminMenuItem');
            if (adminItem) adminItem.style.display = 'block';
        }
    } catch (e) {
        console.error('Load profile error:', e.message);
    }
}

// ============================================
// 导航
// ============================================
function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    event.currentTarget.classList.add('active');
    
    switch(tab) {
        case 'dashboard': loadDashboard(); break;
        case 'knowledge': loadKnowledge(); break;
        case 'vocab': loadVocab(); break;
        case 'reading': loadReading(); break;
        case 'flashcards': loadFlashcards(); break;
        case 'atlas': loadAtlas(); break;
        case 'physiology': loadPhysiology(); break;
        case 'progress': loadProgress(); break;
    }
}

// ============================================
// 首页
// ============================================
async function loadDashboard() {
    const content = document.getElementById('appContent');
    
    // 倒计时（本地计算，不依赖网络）
    const examDate = new Date('2026-12-19');
    const now = new Date();
    const daysLeft = Math.ceil((examDate - now) / (1000 * 60 * 60 * 24));
    
    // 先显示缓存数据或默认值
    const cachedStats = getCache('dashboard_stats', 2 * 60 * 1000);
    const stats = cachedStats || { chapters: 164, vocab: 5716, passages: 64, flashcards: 477 };
    const cachedAnnouncements = getCache('announcements', 10 * 60 * 1000) || [];
    
    content.innerHTML = `
        ${cachedAnnouncements?.length ? `
        <div style="margin-bottom: 16px;">
            ${cachedAnnouncements.map(a => `
                <div class="card" style="background: linear-gradient(135deg, #fef3c7, #fde68a); border-left: 4px solid #f59e0b; margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div>
                            <strong style="color: #92400e;">📢 ${a.title}</strong>
                            <p style="margin: 4px 0 0; color: #78350f; font-size: 14px;">${a.content}</p>
                        </div>
                        <span style="font-size: 11px; color: #92400e; opacity: 0.7;">${a.created_at ? new Date(a.created_at).toLocaleDateString() : ''}</span>
                    </div>
                </div>
            `).join('')}
        </div>` : ''}
        <div class="card" style="background: linear-gradient(135deg, #667eea, #764ba2); color: white;">
            <h2 style="font-size: 24px; margin-bottom: 8px;">距离2026年考研还有 ${daysLeft} 天</h2>
            <p style="opacity: 0.9;">考试时间：2026年12月19日</p>
        </div>
        
        <div class="grid-4" style="margin-bottom: 16px;">
            <div class="stat-card"><div class="stat-icon">📖</div><div class="stat-value" id="statChapters">${stats.chapters || '...'}</div><div class="stat-label">知识章节</div></div>
            <div class="stat-card"><div class="stat-icon">📝</div><div class="stat-value" id="statVocab">${stats.vocab || '...'}</div><div class="stat-label">英语词汇</div></div>
            <div class="stat-card"><div class="stat-icon">📄</div><div class="stat-value" id="statPassages">${stats.passages || '...'}</div><div class="stat-label">真题阅读</div></div>
            <div class="stat-card"><div class="stat-icon">🃏</div><div class="stat-value" id="statFlashcards">${stats.flashcards || '...'}</div><div class="stat-label">记忆闪卡</div></div>
        </div>
        
        <div class="card">
            <div class="card-title">📚 科目导航</div>
            <div class="grid-4">
                <div class="subject-card" onclick="selectSubject(1)">
                    <div class="subject-icon">🔤</div>
                    <div class="subject-name">英语（一）</div>
                    <div class="subject-desc">词汇 · 真题 · 阅读</div>
                </div>
                <div class="subject-card" onclick="selectSubject(2)">
                    <div class="subject-icon">🏛️</div>
                    <div class="subject-name">政治</div>
                    <div class="subject-desc">马原 · 毛中特 · 史纲</div>
                </div>
                <div class="subject-card" onclick="selectSubject(3)">
                    <div class="subject-icon">💪</div>
                    <div class="subject-name">运动生理学</div>
                    <div class="subject-desc">18章核心知识</div>
                </div>
                <div class="subject-card" onclick="selectSubject(4)">
                    <div class="subject-icon">🦴</div>
                    <div class="subject-name">运动解剖学</div>
                    <div class="subject-desc">7章系统详解</div>
                </div>
            </div>
        </div>
    `;
    
    // 后台刷新数据
    (async () => {
        try {
            const results = await Promise.allSettled([
                supabase.from('chapters').select('*', { count: 'exact', head: true }),
                supabase.from('vocab_words').select('*', { count: 'exact', head: true }),
                supabase.from('reading_passages').select('*', { count: 'exact', head: true }),
                supabase.from('flashcards').select('*', { count: 'exact', head: true }),
                supabase.from('announcements').select('*').eq('is_active', true).order('created_at', { ascending: false }).limit(3)
            ]);
            
            const newStats = {
                chapters: results[0].status === 'fulfilled' ? results[0].value.count : stats.chapters,
                vocab: results[1].status === 'fulfilled' ? results[1].value.count : stats.vocab,
                passages: results[2].status === 'fulfilled' ? results[2].value.count : stats.passages,
                flashcards: results[3].status === 'fulfilled' ? results[3].value.count : stats.flashcards
            };
            setCache('dashboard_stats', newStats);
            
            // 更新DOM
            const el1 = document.getElementById('statChapters'); if (el1) el1.textContent = newStats.chapters;
            const el2 = document.getElementById('statVocab'); if (el2) el2.textContent = newStats.vocab;
            const el3 = document.getElementById('statPassages'); if (el3) el3.textContent = newStats.passages;
            const el4 = document.getElementById('statFlashcards'); if (el4) el4.textContent = newStats.flashcards;
            
            // 更新公告
            if (results[4].status === 'fulfilled' && results[4].value.data) {
                setCache('announcements', results[4].value.data);
            }
        } catch (e) {
            console.error('Dashboard refresh error:', e);
        }
    })();
}

function selectSubject(id) {
    currentSubject = id;
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.querySelector('.nav-item[onclick*="knowledge"]').classList.add('active');
    loadKnowledge();
}

// ============================================
// 知识库
// ============================================
async function loadKnowledge() {
    const content = document.getElementById('appContent');
    
    if (!currentSubject) {
        content.innerHTML = `
            <div class="card">
                <div class="card-title">📖 知识库 - 选择科目</div>
                <div class="grid-2">
                    <div class="subject-card" onclick="selectSubject(2)"><div class="subject-icon">🏛️</div><div class="subject-name">政治</div></div>
                    <div class="subject-card" onclick="selectSubject(3)"><div class="subject-icon">💪</div><div class="subject-name">运动生理学</div></div>
                    <div class="subject-card" onclick="selectSubject(4)"><div class="subject-icon">🦴</div><div class="subject-name">运动解剖学</div></div>
                </div>
            </div>
        `;
        return;
    }
    
    // 先显示加载状态
    content.innerHTML = `
        <div style="display: flex; gap: 16px;">
            <div class="card" style="width: 280px; flex-shrink: 0;">
                <div class="card-title">章节列表</div>
                <p style="color:#9ca3af; padding:20px; text-align:center;">⏳ 加载中...</p>
            </div>
            <div class="card" style="flex: 1;">
                <p style="color:#9ca3af; padding:40px; text-align:center;">⏳ 正在加载章节内容...</p>
            </div>
        </div>
    `;
    
    // 使用缓存优先
    const cacheKey = `chapters_${currentSubject}`;
    let chapters = getCache(cacheKey, 10 * 60 * 1000);
    
    if (!chapters) {
        try {
            const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 10000));
            const request = supabase.from('chapters').select('id, title').eq('subject_id', currentSubject).order('sort_order');
            const result = await Promise.race([request, timeout]);
            chapters = result.data || [];
            setCache(cacheKey, chapters);
        } catch (e) {
            console.error('Load chapters error:', e);
            chapters = [];
        }
    }
    
    content.innerHTML = `
        <div style="display: flex; gap: 16px;">
            <div class="card" style="width: 280px; flex-shrink: 0;">
                <div class="card-title">章节列表 (${chapters?.length || 0})</div>
                <div class="chapter-list" id="chapterList">
                    ${chapters?.map((ch, i) => `<div class="chapter-item ${i===0?'active':''}" onclick="loadChapter('${ch.id}', this)">${ch.title}</div>`).join('') || '<p style="color:#9ca3af; padding:20px; text-align:center;">暂无章节<br><small>网络可能较慢，请刷新重试</small></p>'}
                </div>
            </div>
            <div class="card" style="flex: 1;" id="chapterContent">
                <p style="color:#9ca3af; padding:40px; text-align:center;">选择左侧章节查看内容</p>
            </div>
        </div>
    `;
    
    if (chapters && chapters.length > 0) {
        loadChapter(chapters[0].id, document.querySelector('.chapter-item'));
    }
}

async function loadChapter(id, element) {
    document.querySelectorAll('.chapter-item').forEach(item => item.classList.remove('active'));
    if (element) element.classList.add('active');
    
    const chapterContent = document.getElementById('chapterContent');
    if (chapterContent) {
        chapterContent.innerHTML = '<p style="color:#9ca3af; padding:40px; text-align:center;">⏳ 加载中...</p>';
    }
    
    // 缓存优先
    const cacheKey = `chapter_${id}`;
    let chapter = getCache(cacheKey, 30 * 60 * 1000);
    
    if (!chapter) {
        try {
            const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 10000));
            const request = supabase.from('chapters').select('*').eq('id', id).single();
            const result = await Promise.race([request, timeout]);
            chapter = result.data;
            if (chapter) setCache(cacheKey, chapter);
        } catch (e) {
            console.error('Load chapter error:', e);
        }
    }
    
    if (chapterContent) {
        chapterContent.innerHTML = `
            <h2 style="margin-bottom: 16px; font-size: 20px;">${chapter?.title || '加载失败'}</h2>
            <div class="chapter-content">${chapter?.content || '<p style="color:#9ca3af;">内容加载失败，请检查网络后刷新重试</p>'}</div>
        `;
    }
}

// ============================================
// 词汇
// ============================================
async function loadVocab() {
    const content = document.getElementById('appContent');
    content.innerHTML = `
        <div class="card">
            <div class="card-title">📝 英语词汇 (5716词)</div>
            <input type="text" id="vocabSearch" placeholder="🔍 搜索单词..." style="width: 100%; padding: 12px; margin-bottom: 16px; border: 1px solid #e5e7eb; border-radius: 8px;" oninput="searchVocab(this.value)">
            <div id="vocabList" style="max-height: 600px; overflow-y: auto;"></div>
        </div>
    `;
    searchVocab('');
}

async function searchVocab(query) {
    let request = supabase.from('vocab_words').select('*').limit(100);
    if (query) {
        request = request.ilike('word', `%${query}%`);
    }
    const { data } = await request.order('word');
    
    document.getElementById('vocabList').innerHTML = data?.map(w => `
        <div class="vocab-item">
            <span class="vocab-word">${w.word}</span>
            <span class="vocab-meaning">${w.meaning}</span>
            <span class="vocab-category">${w.category || ''}</span>
        </div>
    `).join('') || '<p style="color:#9ca3af; text-align:center; padding:20px;">未找到词汇</p>';
}

// ============================================
// 真题阅读
// ============================================
async function loadReading() {
    const content = document.getElementById('appContent');
    const { data: years } = await supabase.from('reading_passages').select('year').order('year', { ascending: false });
    const uniqueYears = [...new Set(years?.map(y => y.year) || [])];
    
    content.innerHTML = `
        <div class="card">
            <div class="reading-header">
                <div class="card-title" style="margin:0">📄 真题阅读</div>
                <div class="reading-year-select" id="yearSelect">
                    ${uniqueYears.map((y, i) => `<button class="year-btn ${i===0?'active':''}" onclick="selectYear(${y}, this)">${y}年</button>`).join('')}
                </div>
            </div>
            <div id="passageList" style="display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap;"></div>
            <div id="passageContent"></div>
        </div>
    `;
    
    if (uniqueYears.length > 0) selectYear(uniqueYears[0]);
}

async function selectYear(year, element) {
    if (element) {
        document.querySelectorAll('.year-btn').forEach(b => b.classList.remove('active'));
        element.classList.add('active');
    }
    
    const { data: passages } = await supabase.from('reading_passages').select('id, title, passage_number').eq('year', year).order('passage_number');
    
    document.getElementById('passageList').innerHTML = passages?.map((p, i) => 
        `<button class="year-btn ${i===0?'active':''}" onclick="loadPassage('${p.id}', this)">Text ${p.passage_number}</button>`
    ).join('') || '';
    
    if (passages && passages.length > 0) loadPassage(passages[0].id);
}

async function loadPassage(id, element) {
    if (element) {
        document.querySelectorAll('#passageList .year-btn').forEach(b => b.classList.remove('active'));
        element.classList.add('active');
    }
    
    const [{ data: passage }, { data: questions }] = await Promise.all([
        supabase.from('reading_passages').select('*').eq('id', id).single(),
        supabase.from('reading_questions').select('*').eq('passage_id', id).order('sort_order')
    ]);
    
    document.getElementById('passageContent').innerHTML = `
        <h3 style="margin-bottom: 16px;">${passage?.title || ''}</h3>
        <div class="reading-text">${passage?.original_text || ''}</div>
        <div class="reading-translation"><strong>译文：</strong>${passage?.translation || '暂无译文'}</div>
        <div style="margin-top: 24px;">
            <h4 style="margin-bottom: 16px;">题目 (${questions?.length || 0})</h4>
            ${questions?.map((q, i) => `
                <div class="question-item">
                    <span class="question-type">${q.question_type || '细节题'}</span>
                    <div class="question-text">${q.question_number}. ${q.question_text}</div>
                    ${(q.options || []).map((opt, oi) => 
                        `<div class="option-item" onclick="checkAnswer(this, '${String.fromCharCode(65+oi)}', '${q.correct_answer}')">${String.fromCharCode(65+oi)}. ${opt}</div>`
                    ).join('')}
                    <div style="margin-top: 8px; font-size: 13px; color: #6b7280;"><strong>答案：</strong>${q.correct_answer} | <strong>解析：</strong>${q.explanation || '暂无'}</div>
                </div>
            `).join('') || '<p>暂无题目</p>'}
        </div>
    `;
}

function checkAnswer(element, selected, correct) {
    const parent = element.parentElement;
    parent.querySelectorAll('.option-item').forEach(opt => {
        opt.classList.remove('correct', 'wrong');
    });
    if (selected === correct) {
        element.classList.add('correct');
    } else {
        element.classList.add('wrong');
        parent.querySelectorAll('.option-item').forEach((opt, i) => {
            if (String.fromCharCode(65+i) === correct) opt.classList.add('correct');
        });
    }
}

// ============================================
// 闪卡
// ============================================
async function loadFlashcards() {
    const content = document.getElementById('appContent');
    const { data: flashcards } = await supabase.from('flashcards').select('*').limit(500);
    allFlashcards = flashcards || [];
    flashcardIndex = 0;
    
    content.innerHTML = `
        <div class="card">
            <div class="card-title">🃏 记忆闪卡 (${allFlashcards.length}张)</div>
            <div class="flashcard-container">
                <div class="flashcard" id="flashcard" onclick="flipFlashcard()">
                    <div class="flashcard-face flashcard-front" id="flashcardFront"></div>
                    <div class="flashcard-face flashcard-back" id="flashcardBack"></div>
                </div>
                <div style="text-align: center; margin-top: 12px; color: #6b7280;">
                    <span id="flashcardProgress">1 / ${allFlashcards.length}</span>
                </div>
                <div class="flashcard-controls">
                    <button class="btn btn-secondary" onclick="prevFlashcard()">上一张</button>
                    <button class="btn btn-danger" onclick="rateFlashcard('again')">不认识</button>
                    <button class="btn btn-success" onclick="rateFlashcard('known')">认识</button>
                    <button class="btn btn-secondary" onclick="nextFlashcard()">下一张</button>
                </div>
            </div>
        </div>
    `;
    
    showFlashcard();
}

function showFlashcard() {
    if (allFlashcards.length === 0) return;
    const card = allFlashcards[flashcardIndex];
    document.getElementById('flashcardFront').textContent = card.front;
    document.getElementById('flashcardBack').textContent = card.back;
    document.getElementById('flashcardProgress').textContent = `${flashcardIndex + 1} / ${allFlashcards.length}`;
    document.getElementById('flashcard').classList.remove('flipped');
    flashcardFlipped = false;
}

function flipFlashcard() {
    flashcardFlipped = !flashcardFlipped;
    document.getElementById('flashcard').classList.toggle('flipped');
}

function nextFlashcard() {
    flashcardIndex = (flashcardIndex + 1) % allFlashcards.length;
    showFlashcard();
}

function prevFlashcard() {
    flashcardIndex = (flashcardIndex - 1 + allFlashcards.length) % allFlashcards.length;
    showFlashcard();
}

function rateFlashcard(rating) {
    nextFlashcard();
}

// ============================================
// 解剖图谱
// ============================================
async function loadAtlas() {
    const content = document.getElementById('appContent');
    const { data: systems } = await supabase.from('anatomy_systems').select('*');
    
    content.innerHTML = `
        <div class="card">
            <div class="card-title">🦴 人体解剖学图谱</div>
            <div class="atlas-system-grid" id="atlasGrid">
                ${systems?.map(s => `
                    <div class="atlas-system-card" style="background: ${s.color || '#667eea'}" onclick="loadAtlasSystem('${s.system_key}')">
                        <div class="atlas-system-icon">${s.icon || '📌'}</div>
                        <div class="atlas-system-name">${s.name}</div>
                    </div>
                `).join('') || '<p>暂无数据</p>'}
            </div>
            <div id="atlasDetail" style="margin-top: 24px;"></div>
        </div>
    `;
}

async function loadAtlasSystem(key) {
    const { data: system } = await supabase.from('anatomy_systems').select('*').eq('system_key', key).single();
    
    document.getElementById('atlasDetail').innerHTML = `
        <div class="card">
            <h3 style="margin-bottom: 16px;">${system?.icon || ''} ${system?.name || ''}</h3>
            <p style="line-height: 1.8; margin-bottom: 16px;">${system?.overview || ''}</p>
            ${system?.divisions?.length ? `<h4 style="margin-bottom: 12px;">系统分部</h4><ul style="line-height: 2; padding-left: 20px;">${system.divisions.map(d => `<li>${d}</li>`).join('')}</ul>` : ''}
            ${system?.exam_points?.length ? `<h4 style="margin: 16px 0 12px;">考研考点</h4><ul style="line-height: 2; padding-left: 20px;">${system.exam_points.map(p => `<li>${p}</li>`).join('')}</ul>` : ''}
        </div>
    `;
}

// ============================================
// 生理图谱
// ============================================
async function loadPhysiology() {
    const content = document.getElementById('appContent');
    const { data: concepts } = await supabase.from('physiology_concepts').select('*');
    
    content.innerHTML = `
        <div class="card">
            <div class="card-title">💪 生理学知识图谱</div>
            <div class="grid-2" id="physioGrid">
                ${concepts?.map(c => `
                    <div class="subject-card" onclick="loadPhysioConcept('${c.concept_key}')">
                        <div class="subject-name" style="font-size: 16px;">${c.name}</div>
                        <div class="subject-desc" style="margin-top: 8px;">${(c.easy_understand || '').substring(0, 50)}...</div>
                    </div>
                `).join('') || '<p>暂无数据</p>'}
            </div>
            <div id="physioDetail" style="margin-top: 24px;"></div>
        </div>
    `;
}

async function loadPhysioConcept(key) {
    const { data: concept } = await supabase.from('physiology_concepts').select('*').eq('concept_key', key).single();
    
    document.getElementById('physioDetail').innerHTML = `
        <div class="card">
            <h3 style="margin-bottom: 16px;">${concept?.name || ''}</h3>
            <div style="background: #fef3c7; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                <strong>💡 通俗理解：</strong>${concept?.easy_understand || ''}
            </div>
            ${concept?.key_concepts?.length ? `<h4 style="margin-bottom: 12px;">核心概念</h4><ul style="line-height: 2; padding-left: 20px;">${concept.key_concepts.map(k => `<li>${k}</li>`).join('')}</ul>` : ''}
            ${concept?.clinical_relevance ? `<div style="margin-top: 16px; background: #dbeafe; padding: 16px; border-radius: 8px;"><strong>🏥 临床联系：</strong>${concept.clinical_relevance}</div>` : ''}
        </div>
    `;
}

// ============================================
// 学习进度
// ============================================
async function loadProgress() {
    const content = document.getElementById('appContent');
    content.innerHTML = `
        <div class="card">
            <div class="card-title">📊 学习进度</div>
            <p style="color: #6b7280;">学习进度功能开发中...</p>
        </div>
    `;
}

// ============================================
// 全局搜索
// ============================================
async function handleGlobalSearch(event) {
    if (event.key !== 'Enter') return;
    const query = event.target.value;
    if (!query) return;
    
    // 搜索词汇
    const { data: vocabResults } = await supabase.from('vocab_words').select('*').ilike('word', `%${query}%`).limit(10);
    // 搜索章节
    const { data: chapterResults } = await supabase.from('chapters').select('id, title, subject_id').ilike('title', `%${query}%`).limit(10);
    
    const content = document.getElementById('appContent');
    content.innerHTML = `
        <div class="card">
            <div class="card-title">🔍 搜索结果: "${query}"</div>
            ${vocabResults?.length ? `<h4 style="margin-bottom: 12px;">词汇 (${vocabResults.length})</h4>${vocabResults.map(v => `<div class="vocab-item"><span class="vocab-word">${v.word}</span><span class="vocab-meaning">${v.meaning}</span></div>`).join('')}` : ''}
            ${chapterResults?.length ? `<h4 style="margin: 16px 0 12px;">章节 (${chapterResults.length})</h4>${chapterResults.map(c => `<div class="chapter-item" onclick="currentSubject=${c.subject_id};loadKnowledge();setTimeout(()=>loadChapter('${c.id}'),500)">${c.title}</div>`).join('')}` : ''}
            ${!vocabResults?.length && !chapterResults?.length ? '<p style="color:#9ca3af">未找到相关内容</p>' : ''}
        </div>
    `;
}

// ============================================
// 管理员后台
// ============================================
function showAdminPanel() {
    document.getElementById('adminModal').style.display = 'flex';
    switchAdminTab('invite');
}

function closeAdminPanel() {
    document.getElementById('adminModal').style.display = 'none';
}

function switchAdminTab(tab) {
    document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    
    if (tab === 'invite') loadInviteCodes();
    else if (tab === 'users') loadAdminUsers();
    else if (tab === 'announcements') loadAdminAnnouncements();
    else if (tab === 'content') loadAdminContent();
    else if (tab === 'stats') loadAdminStats();
}

async function loadInviteCodes() {
    const { data: codes } = await supabase.from('invite_codes').select('*').order('created_at', { ascending: false });
    const unused = codes?.filter(c => !c.is_used).length || 0;
    const used = codes?.filter(c => c.is_used).length || 0;
    
    document.getElementById('adminContent').innerHTML = `
        <div style="display: flex; gap: 12px; margin-bottom: 16px; align-items: center;">
            <button class="btn btn-primary" style="width: auto;" onclick="generateInviteCode()">➕ 生成1个</button>
            <button class="btn btn-secondary" style="width: auto;" onclick="batchGenerateInviteCodes(10)">📦 批量生成10个</button>
            <button class="btn btn-secondary" style="width: auto;" onclick="batchGenerateInviteCodes(50)">📦 批量生成50个</button>
            <span style="margin-left: auto; color: #6b7280; font-size: 14px;">
                未使用: <strong style="color: #10b981;">${unused}</strong> | 已使用: <strong style="color: #ef4444;">${used}</strong> | 总计: <strong>${codes?.length || 0}</strong>
            </span>
        </div>
        <div id="batchResult" style="display: none; background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 12px; margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <strong>✅ 批量生成成功！</strong>
                <button class="btn btn-secondary" style="width: auto; padding: 4px 12px; font-size: 12px;" onclick="copyBatchCodes()">📋 复制全部</button>
            </div>
            <div id="batchCodes" style="font-family: monospace; font-size: 13px; line-height: 1.8;"></div>
        </div>
        <table style="width: 100%; border-collapse: collapse;">
            <thead><tr style="border-bottom: 2px solid #e5e7eb;">
                <th style="text-align: left; padding: 8px;">邀请码</th>
                <th style="text-align: left; padding: 8px;">状态</th>
                <th style="text-align: left; padding: 8px;">使用者</th>
                <th style="text-align: left; padding: 8px;">创建时间</th>
                <th style="text-align: left; padding: 8px;">操作</th>
            </tr></thead>
            <tbody>
                ${codes?.map(c => `<tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px; font-family: monospace; font-weight: bold;">${c.code}</td>
                    <td style="padding: 8px;"><span style="color: ${c.is_used ? '#ef4444' : '#10b981'}; font-weight: 500;">${c.is_used ? '已使用' : '未使用'}</span></td>
                    <td style="padding: 8px;">${c.used_by || '-'}</td>
                    <td style="padding: 8px; font-size: 12px; color: #6b7280;">${c.created_at ? new Date(c.created_at).toLocaleString() : '-'}</td>
                    <td style="padding: 8px;">
                        ${!c.is_used ? `<button class="btn btn-danger" style="width: auto; padding: 4px 10px; font-size: 12px;" onclick="deleteInviteCode('${c.id}')">删除</button>` : '-'}
                    </td>
                </tr>`).join('') || '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #9ca3af;">暂无邀请码</td></tr>'}
            </tbody>
        </table>
    `;
}

let lastBatchCodes = [];

async function batchGenerateInviteCodes(count) {
    const codes = [];
    for (let i = 0; i < count; i++) {
        codes.push({ code: Math.random().toString(36).substring(2, 10).toUpperCase(), created_by: currentUser.id });
    }
    const { error } = await supabase.from('invite_codes').insert(codes);
    if (error) { showToast('生成失败: ' + error.message); return; }
    
    lastBatchCodes = codes.map(c => c.code);
    showToast(`成功生成 ${count} 个邀请码！`);
    loadInviteCodes();
    
    setTimeout(() => {
        const batchDiv = document.getElementById('batchResult');
        if (batchDiv) {
            batchDiv.style.display = 'block';
            document.getElementById('batchCodes').innerHTML = lastBatchCodes.map(c => `<span style="display: inline-block; background: white; padding: 2px 8px; margin: 2px; border-radius: 4px; border: 1px solid #d1d5db;">${c}</span>`).join('');
        }
    }, 100);
}

function copyBatchCodes() {
    navigator.clipboard.writeText(lastBatchCodes.join('\n'));
    showToast('已复制到剪贴板！');
}

async function deleteInviteCode(id) {
    if (!confirm('确定删除这个邀请码吗？')) return;
    const { error } = await supabase.from('invite_codes').delete().eq('id', id);
    if (error) { showToast('删除失败: ' + error.message); return; }
    showToast('邀请码已删除');
    loadInviteCodes();
}

async function generateInviteCode() {
    const code = Math.random().toString(36).substring(2, 10).toUpperCase();
    const { error } = await supabase.from('invite_codes').insert([{ code, created_by: currentUser.id }]);
    if (error) { showToast('生成失败: ' + error.message); return; }
    showToast('邀请码已生成: ' + code);
    loadInviteCodes();
}

async function loadAdminUsers() {
    const { data: users } = await supabase.from('profiles').select('*').order('created_at', { ascending: false });
    const adminCount = users?.filter(u => u.role === 'admin').length || 0;
    
    document.getElementById('adminContent').innerHTML = `
        <div style="margin-bottom: 16px; color: #6b7280; font-size: 14px;">
            总用户: <strong>${users?.length || 0}</strong> | 管理员: <strong style="color: #667eea;">${adminCount}</strong> | 普通用户: <strong>${(users?.length || 0) - adminCount}</strong>
        </div>
        <table style="width: 100%; border-collapse: collapse;">
            <thead><tr style="border-bottom: 2px solid #e5e7eb;">
                <th style="text-align: left; padding: 8px;">用户名</th>
                <th style="text-align: left; padding: 8px;">邮箱</th>
                <th style="text-align: left; padding: 8px;">角色</th>
                <th style="text-align: left; padding: 8px;">注册时间</th>
                <th style="text-align: left; padding: 8px;">操作</th>
            </tr></thead>
            <tbody>
                ${users?.map(u => `<tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px; font-weight: 500;">${u.username || '-'}</td>
                    <td style="padding: 8px; font-size: 13px;">${u.email || '-'}</td>
                    <td style="padding: 8px;"><span style="padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; background: ${u.role === 'admin' ? '#ede9fe' : '#f3f4f6'}; color: ${u.role === 'admin' ? '#667eea' : '#6b7280'};">${u.role === 'admin' ? '管理员' : '普通用户'}</span></td>
                    <td style="padding: 8px; font-size: 12px; color: #6b7280;">${u.created_at ? new Date(u.created_at).toLocaleDateString() : '-'}</td>
                    <td style="padding: 8px;">
                        <div style="display: flex; gap: 6px;">
                            ${u.role === 'admin' 
                                ? `<button class="btn btn-secondary" style="width: auto; padding: 4px 10px; font-size: 12px;" onclick="toggleUserRole('${u.id}', 'user')">降级为用户</button>`
                                : `<button class="btn btn-primary" style="width: auto; padding: 4px 10px; font-size: 12px;" onclick="toggleUserRole('${u.id}', 'admin')">提升为管理员</button>`
                            }
                            ${u.id !== currentUser.id ? `<button class="btn btn-danger" style="width: auto; padding: 4px 10px; font-size: 12px;" onclick="deleteUser('${u.id}')">删除</button>` : ''}
                        </div>
                    </td>
                </tr>`).join('') || '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #9ca3af;">暂无用户</td></tr>'}
            </tbody>
        </table>
    `;
}

async function toggleUserRole(userId, newRole) {
    const roleText = newRole === 'admin' ? '管理员' : '普通用户';
    if (!confirm(`确定将该用户${newRole === 'admin' ? '提升' : '降级'}为${roleText}吗？`)) return;
    const { error } = await supabase.from('profiles').update({ role: newRole }).eq('id', userId);
    if (error) { showToast('操作失败: ' + error.message); return; }
    showToast(`已${newRole === 'admin' ? '提升' : '降级'}为${roleText}`);
    loadAdminUsers();
}

async function deleteUser(userId) {
    if (!confirm('确定删除该用户吗？此操作不可恢复！')) return;
    // 删除profiles记录
    const { error: profileError } = await supabase.from('profiles').delete().eq('id', userId);
    if (profileError) { showToast('删除失败: ' + profileError.message); return; }
    showToast('用户已删除');
    loadAdminUsers();
}

// ============================================
// 管理员 - 公告管理
// ============================================
async function loadAdminAnnouncements() {
    const { data: announcements } = await supabase.from('announcements').select('*').order('created_at', { ascending: false });
    
    document.getElementById('adminContent').innerHTML = `
        <div style="margin-bottom: 16px;">
            <button class="btn btn-primary" style="width: auto;" onclick="showAnnouncementForm()">📢 发布新公告</button>
        </div>
        <div id="announcementForm" style="display: none; background: #f9fafb; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
            <input type="text" id="annTitle" placeholder="公告标题" style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #d1d5db; border-radius: 6px;">
            <textarea id="annContent" placeholder="公告内容" rows="4" style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #d1d5db; border-radius: 6px; resize: vertical;"></textarea>
            <div style="display: flex; gap: 8px;">
                <button class="btn btn-primary" style="width: auto;" onclick="submitAnnouncement()">发布</button>
                <button class="btn btn-secondary" style="width: auto;" onclick="hideAnnouncementForm()">取消</button>
            </div>
        </div>
        <table style="width: 100%; border-collapse: collapse;">
            <thead><tr style="border-bottom: 2px solid #e5e7eb;">
                <th style="text-align: left; padding: 8px;">标题</th>
                <th style="text-align: left; padding: 8px;">内容</th>
                <th style="text-align: left; padding: 8px;">状态</th>
                <th style="text-align: left; padding: 8px;">发布时间</th>
                <th style="text-align: left; padding: 8px;">操作</th>
            </tr></thead>
            <tbody>
                ${announcements?.map(a => `<tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px; font-weight: 500;">${a.title}</td>
                    <td style="padding: 8px; font-size: 13px; color: #6b7280; max-width: 300px;">${(a.content || '').substring(0, 50)}${a.content?.length > 50 ? '...' : ''}</td>
                    <td style="padding: 8px;"><span style="padding: 2px 8px; border-radius: 4px; font-size: 12px; background: ${a.is_active ? '#dcfce7' : '#fef2f2'}; color: ${a.is_active ? '#16a34a' : '#dc2626'};">${a.is_active ? '显示中' : '已隐藏'}</span></td>
                    <td style="padding: 8px; font-size: 12px; color: #6b7280;">${a.created_at ? new Date(a.created_at).toLocaleDateString() : '-'}</td>
                    <td style="padding: 8px;">
                        <div style="display: flex; gap: 6px;">
                            <button class="btn btn-secondary" style="width: auto; padding: 4px 10px; font-size: 12px;" onclick="toggleAnnouncement('${a.id}', ${!a.is_active})">${a.is_active ? '隐藏' : '显示'}</button>
                            <button class="btn btn-danger" style="width: auto; padding: 4px 10px; font-size: 12px;" onclick="deleteAnnouncement('${a.id}')">删除</button>
                        </div>
                    </td>
                </tr>`).join('') || '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #9ca3af;">暂无公告</td></tr>'}
            </tbody>
        </table>
    `;
}

function showAnnouncementForm() {
    document.getElementById('announcementForm').style.display = 'block';
}

function hideAnnouncementForm() {
    document.getElementById('announcementForm').style.display = 'none';
    document.getElementById('annTitle').value = '';
    document.getElementById('annContent').value = '';
}

async function submitAnnouncement() {
    const title = document.getElementById('annTitle').value;
    const content = document.getElementById('annContent').value;
    if (!title || !content) { showToast('请填写标题和内容'); return; }
    
    const { error } = await supabase.from('announcements').insert([{ title, content, created_by: currentUser.id }]);
    if (error) { showToast('发布失败: ' + error.message); return; }
    showToast('公告发布成功！');
    hideAnnouncementForm();
    loadAdminAnnouncements();
}

async function toggleAnnouncement(id, isActive) {
    const { error } = await supabase.from('announcements').update({ is_active: isActive }).eq('id', id);
    if (error) { showToast('操作失败: ' + error.message); return; }
    showToast(isActive ? '公告已显示' : '公告已隐藏');
    loadAdminAnnouncements();
}

async function deleteAnnouncement(id) {
    if (!confirm('确定删除这个公告吗？')) return;
    const { error } = await supabase.from('announcements').delete().eq('id', id);
    if (error) { showToast('删除失败: ' + error.message); return; }
    showToast('公告已删除');
    loadAdminAnnouncements();
}

// ============================================
// 管理员 - 内容管理
// ============================================
async function loadAdminContent() {
    const [{ count: chapters }, { count: vocab }, { count: passages }, { count: flashcards }] = await Promise.all([
        supabase.from('chapters').select('*', { count: 'exact', head: true }),
        supabase.from('vocab_words').select('*', { count: 'exact', head: true }),
        supabase.from('reading_passages').select('*', { count: 'exact', head: true }),
        supabase.from('flashcards').select('*', { count: 'exact', head: true })
    ]);
    
    document.getElementById('adminContent').innerHTML = `
        <div class="grid-4" style="margin-bottom: 20px;">
            <div class="stat-card" onclick="loadContentEditor('chapters')" style="cursor: pointer;">
                <div class="stat-icon">📖</div><div class="stat-value">${chapters || 0}</div><div class="stat-label">知识章节</div>
            </div>
            <div class="stat-card" onclick="loadContentEditor('vocab')" style="cursor: pointer;">
                <div class="stat-icon">📝</div><div class="stat-value">${vocab || 0}</div><div class="stat-label">英语词汇</div>
            </div>
            <div class="stat-card" onclick="loadContentEditor('passages')" style="cursor: pointer;">
                <div class="stat-icon">📄</div><div class="stat-value">${passages || 0}</div><div class="stat-label">真题阅读</div>
            </div>
            <div class="stat-card" onclick="loadContentEditor('flashcards')" style="cursor: pointer;">
                <div class="stat-icon">🃏</div><div class="stat-value">${flashcards || 0}</div><div class="stat-label">记忆闪卡</div>
            </div>
        </div>
        <div class="card" style="background: #fef3c7; border-left: 4px solid #f59e0b;">
            <p style="margin: 0; color: #92400e;"><strong>💡 提示：</strong>点击上方卡片进入对应内容的编辑器，可以添加、编辑、删除数据。修改后所有用户立即可见。</p>
        </div>
        <div id="contentEditor" style="margin-top: 20px;"></div>
    `;
}

async function loadContentEditor(type) {
    const editor = document.getElementById('contentEditor');
    
    if (type === 'chapters') {
        const { data: chapters } = await supabase.from('chapters').select('id, title, subject_id, sort_order').order('subject_id').order('sort_order');
        const subjectNames = { 1: '英语', 2: '政治', 3: '运动生理学', 4: '运动解剖学' };
        
        editor.innerHTML = `
            <div class="card">
                <div class="card-title">📖 章节管理</div>
                <div style="margin-bottom: 12px;">
                    <button class="btn btn-primary" style="width: auto;" onclick="showChapterForm()">➕ 添加章节</button>
                </div>
                <div id="chapterForm" style="display: none; background: #f9fafb; padding: 12px; border-radius: 8px; margin-bottom: 12px;">
                    <select id="chSubject" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px;">
                        <option value="2">政治</option>
                        <option value="3">运动生理学</option>
                        <option value="4">运动解剖学</option>
                    </select>
                    <input type="text" id="chTitle" placeholder="章节标题" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px; width: 200px;">
                    <button class="btn btn-primary" style="width: auto;" onclick="addChapter()">添加</button>
                    <button class="btn btn-secondary" style="width: auto;" onclick="document.getElementById('chapterForm').style.display='none'">取消</button>
                </div>
                <div style="max-height: 400px; overflow-y: auto;">
                    ${chapters?.map(c => `<div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; border-bottom: 1px solid #f3f4f6;">
                        <div><span style="font-size: 11px; color: #9ca3af; margin-right: 8px;">[${subjectNames[c.subject_id] || c.subject_id}]</span>${c.title}</div>
                        <button class="btn btn-danger" style="width: auto; padding: 2px 8px; font-size: 11px;" onclick="deleteChapter('${c.id}')">删除</button>
                    </div>`).join('') || '<p style="color:#9ca3af">暂无章节</p>'}
                </div>
            </div>
        `;
    } else if (type === 'vocab') {
        editor.innerHTML = `
            <div class="card">
                <div class="card-title">📝 词汇管理</div>
                <div style="margin-bottom: 12px;">
                    <input type="text" id="newWord" placeholder="单词" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px; width: 120px;">
                    <input type="text" id="newMeaning" placeholder="释义" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px; width: 200px;">
                    <select id="newCategory" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px;">
                        <option value="必考词">必考词</option>
                        <option value="基础词">基础词</option>
                        <option value="超纲词">超纲词</option>
                    </select>
                    <button class="btn btn-primary" style="width: auto;" onclick="addVocab()">添加</button>
                </div>
                <p style="color: #6b7280; font-size: 13px;">词汇量较大，删除请在搜索后操作。当前支持添加新词汇。</p>
            </div>
        `;
    } else if (type === 'flashcards') {
        const { data: flashcards } = await supabase.from('flashcards').select('*').limit(50).order('id');
        editor.innerHTML = `
            <div class="card">
                <div class="card-title">🃏 闪卡管理 (显示前50张)</div>
                <div style="margin-bottom: 12px;">
                    <input type="text" id="fcFront" placeholder="正面（问题）" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px; width: 200px;">
                    <input type="text" id="fcBack" placeholder="背面（答案）" style="padding: 8px; margin-right: 8px; border: 1px solid #d1d5db; border-radius: 6px; width: 200px;">
                    <button class="btn btn-primary" style="width: auto;" onclick="addFlashcard()">添加</button>
                </div>
                <div style="max-height: 400px; overflow-y: auto;">
                    ${flashcards?.map(f => `<div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; border-bottom: 1px solid #f3f4f6;">
                        <div style="flex: 1;"><strong>${f.front}</strong><br><span style="color: #6b7280; font-size: 13px;">${f.back}</span></div>
                        <button class="btn btn-danger" style="width: auto; padding: 2px 8px; font-size: 11px;" onclick="deleteFlashcard(${f.id})">删除</button>
                    </div>`).join('') || '<p style="color:#9ca3af">暂无闪卡</p>'}
                </div>
            </div>
        `;
    } else {
        editor.innerHTML = '<div class="card"><p style="color:#9ca3af;">该内容类型管理功能开发中...</p></div>';
    }
}

function showChapterForm() {
    document.getElementById('chapterForm').style.display = 'block';
}

async function addChapter() {
    const subjectId = parseInt(document.getElementById('chSubject').value);
    const title = document.getElementById('chTitle').value;
    if (!title) { showToast('请输入章节标题'); return; }
    
    const id = `${subjectId === 2 ? 'pol' : subjectId === 3 ? 'phy' : 'ana'}-${Date.now()}`;
    const { error } = await supabase.from('chapters').insert([{ id, subject_id: subjectId, chapter_key: id, title, content: '<p>待编辑内容...</p>', sort_order: 999 }]);
    if (error) { showToast('添加失败: ' + error.message); return; }
    showToast('章节添加成功！');
    document.getElementById('chTitle').value = '';
    loadContentEditor('chapters');
}

async function deleteChapter(id) {
    if (!confirm('确定删除这个章节吗？')) return;
    const { error } = await supabase.from('chapters').delete().eq('id', id);
    if (error) { showToast('删除失败: ' + error.message); return; }
    showToast('章节已删除');
    loadContentEditor('chapters');
}

async function addVocab() {
    const word = document.getElementById('newWord').value;
    const meaning = document.getElementById('newMeaning').value;
    const category = document.getElementById('newCategory').value;
    if (!word || !meaning) { showToast('请输入单词和释义'); return; }
    
    const { error } = await supabase.from('vocab_words').insert([{ word, meaning, category }]);
    if (error) { showToast('添加失败: ' + error.message); return; }
    showToast('词汇添加成功！');
    document.getElementById('newWord').value = '';
    document.getElementById('newMeaning').value = '';
}

async function addFlashcard() {
    const front = document.getElementById('fcFront').value;
    const back = document.getElementById('fcBack').value;
    if (!front || !back) { showToast('请输入正面和背面内容'); return; }
    
    const { error } = await supabase.from('flashcards').insert([{ subject_id: 2, category: '自定义', front, back, explanation: '' }]);
    if (error) { showToast('添加失败: ' + error.message); return; }
    showToast('闪卡添加成功！');
    document.getElementById('fcFront').value = '';
    document.getElementById('fcBack').value = '';
    loadContentEditor('flashcards');
}

async function deleteFlashcard(id) {
    if (!confirm('确定删除这张闪卡吗？')) return;
    const { error } = await supabase.from('flashcards').delete().eq('id', id);
    if (error) { showToast('删除失败: ' + error.message); return; }
    showToast('闪卡已删除');
    loadContentEditor('flashcards');
}

// ============================================
// 管理员 - 数据统计
// ============================================
    const [{ count: chapters }, { count: vocab }, { count: passages }, { count: flashcards }, { count: users }] = await Promise.all([
        supabase.from('chapters').select('*', { count: 'exact', head: true }),
        supabase.from('vocab_words').select('*', { count: 'exact', head: true }),
        supabase.from('reading_passages').select('*', { count: 'exact', head: true }),
        supabase.from('flashcards').select('*', { count: 'exact', head: true }),
        supabase.from('profiles').select('*', { count: 'exact', head: true })
    ]);
    
    document.getElementById('adminContent').innerHTML = `
        <div class="grid-4">
            <div class="stat-card"><div class="stat-icon">📖</div><div class="stat-value">${chapters || 0}</div><div class="stat-label">知识章节</div></div>
            <div class="stat-card"><div class="stat-icon">📝</div><div class="stat-value">${vocab || 0}</div><div class="stat-label">英语词汇</div></div>
            <div class="stat-card"><div class="stat-icon">📄</div><div class="stat-value">${passages || 0}</div><div class="stat-label">真题阅读</div></div>
            <div class="stat-card"><div class="stat-icon">🃏</div><div class="stat-value">${flashcards || 0}</div><div class="stat-label">记忆闪卡</div></div>
            <div class="stat-card"><div class="stat-icon">👥</div><div class="stat-value">${users || 0}</div><div class="stat-label">注册用户</div></div>
        </div>
    `;
}

// ============================================
// 工具函数
// ============================================
function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

function toggleUserMenu() {
    const menu = document.getElementById('userMenu');
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

function showProfile() {
    document.getElementById('userMenu').style.display = 'none';
    showToast('个人中心开发中...');
}

// 点击外部关闭菜单
document.addEventListener('click', (e) => {
    if (!e.target.closest('.user-info')) {
        document.getElementById('userMenu').style.display = 'none';
    }
});

// 启动
init();
