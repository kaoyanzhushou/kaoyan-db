// 考研资料库 - 全栈版前端逻辑
const SUPABASE_URL = 'https://ifeslngcdledzgwltcbl.supabase.co';
const SUPABASE_ANON_KEY = 'sb_publishable_8PV65BZfwPABIMYw8zJMSg_y0q6pQrL';

const supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

let currentUser = null;
let currentTab = 'dashboard';
let currentSubject = null;
let currentChapter = null;
let flashcardIndex = 0;
let flashcardFlipped = false;
let allFlashcards = [];

// ============================================
// 初始化
// ============================================
async function init() {
    updateLoading('检查登录状态...', 20);
    
    // 检查登录状态
    const { data: { session } } = await supabase.auth.getSession();
    if (session) {
        currentUser = session.user;
        await loadUserProfile();
        showMainApp();
    } else {
        showLoginPage();
    }
    
    updateLoading('加载完成', 100);
    setTimeout(() => {
        document.getElementById('loadingScreen').style.display = 'none';
    }, 500);
}

function updateLoading(text, progress) {
    document.getElementById('loadingText').textContent = text;
    document.getElementById('loadingProgress').style.width = progress + '%';
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
    const { data } = await supabase.from('profiles').select('*').eq('id', currentUser.id).single();
    if (data && data.role === 'admin') {
        document.getElementById('adminMenuItem').style.display = 'block';
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
    
    // 获取统计数据
    const [{ count: chapterCount }, { count: vocabCount }, { count: passageCount }, { count: flashcardCount }] = await Promise.all([
        supabase.from('chapters').select('*', { count: 'exact', head: true }),
        supabase.from('vocab_words').select('*', { count: 'exact', head: true }),
        supabase.from('reading_passages').select('*', { count: 'exact', head: true }),
        supabase.from('flashcards').select('*', { count: 'exact', head: true })
    ]);
    
    // 倒计时
    const examDate = new Date('2026-12-19');
    const now = new Date();
    const daysLeft = Math.ceil((examDate - now) / (1000 * 60 * 60 * 24));
    
    content.innerHTML = `
        <div class="card" style="background: linear-gradient(135deg, #667eea, #764ba2); color: white;">
            <h2 style="font-size: 24px; margin-bottom: 8px;">距离2026年考研还有 ${daysLeft} 天</h2>
            <p style="opacity: 0.9;">考试时间：2026年12月19日</p>
        </div>
        
        <div class="grid-4" style="margin-bottom: 16px;">
            <div class="stat-card"><div class="stat-icon">📖</div><div class="stat-value">${chapterCount || 0}</div><div class="stat-label">知识章节</div></div>
            <div class="stat-card"><div class="stat-icon">📝</div><div class="stat-value">${vocabCount || 0}</div><div class="stat-label">英语词汇</div></div>
            <div class="stat-card"><div class="stat-icon">📄</div><div class="stat-value">${passageCount || 0}</div><div class="stat-label">真题阅读</div></div>
            <div class="stat-card"><div class="stat-icon">🃏</div><div class="stat-value">${flashcardCount || 0}</div><div class="stat-label">记忆闪卡</div></div>
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
    
    const { data: chapters } = await supabase.from('chapters').select('id, title').eq('subject_id', currentSubject).order('sort_order');
    
    content.innerHTML = `
        <div style="display: flex; gap: 16px;">
            <div class="card" style="width: 280px; flex-shrink: 0;">
                <div class="card-title">章节列表 (${chapters?.length || 0})</div>
                <div class="chapter-list" id="chapterList">
                    ${chapters?.map((ch, i) => `<div class="chapter-item ${i===0?'active':''}" onclick="loadChapter('${ch.id}', this)">${ch.title}</div>`).join('') || '<p style="color:#9ca3af">暂无章节</p>'}
                </div>
            </div>
            <div class="card" style="flex: 1;" id="chapterContent">
                <p style="color:#9ca3af">选择左侧章节查看内容</p>
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
    
    const { data: chapter } = await supabase.from('chapters').select('*').eq('id', id).single();
    
    document.getElementById('chapterContent').innerHTML = `
        <h2 style="margin-bottom: 16px; font-size: 20px;">${chapter?.title || ''}</h2>
        <div class="chapter-content">${chapter?.content || '<p>暂无内容</p>'}</div>
    `;
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
    else if (tab === 'stats') loadAdminStats();
}

async function loadInviteCodes() {
    const { data: codes } = await supabase.from('invite_codes').select('*').order('created_at', { ascending: false });
    
    document.getElementById('adminContent').innerHTML = `
        <div style="margin-bottom: 16px;">
            <button class="btn btn-primary" style="width: auto;" onclick="generateInviteCode()">生成邀请码</button>
        </div>
        <table style="width: 100%; border-collapse: collapse;">
            <thead><tr style="border-bottom: 2px solid #e5e7eb;">
                <th style="text-align: left; padding: 8px;">邀请码</th>
                <th style="text-align: left; padding: 8px;">状态</th>
                <th style="text-align: left; padding: 8px;">使用者</th>
                <th style="text-align: left; padding: 8px;">创建时间</th>
            </tr></thead>
            <tbody>
                ${codes?.map(c => `<tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px; font-family: monospace;">${c.code}</td>
                    <td style="padding: 8px;"><span style="color: ${c.is_used ? '#ef4444' : '#10b981'};">${c.is_used ? '已使用' : '未使用'}</span></td>
                    <td style="padding: 8px;">${c.used_by || '-'}</td>
                    <td style="padding: 8px; font-size: 12px; color: #6b7280;">${c.created_at ? new Date(c.created_at).toLocaleDateString() : '-'}</td>
                </tr>`).join('') || '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #9ca3af;">暂无邀请码</td></tr>'}
            </tbody>
        </table>
    `;
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
    
    document.getElementById('adminContent').innerHTML = `
        <table style="width: 100%; border-collapse: collapse;">
            <thead><tr style="border-bottom: 2px solid #e5e7eb;">
                <th style="text-align: left; padding: 8px;">用户名</th>
                <th style="text-align: left; padding: 8px;">邮箱</th>
                <th style="text-align: left; padding: 8px;">角色</th>
                <th style="text-align: left; padding: 8px;">注册时间</th>
            </tr></thead>
            <tbody>
                ${users?.map(u => `<tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px;">${u.username || '-'}</td>
                    <td style="padding: 8px;">${u.email || '-'}</td>
                    <td style="padding: 8px;"><span style="color: ${u.role === 'admin' ? '#667eea' : '#6b7280'};">${u.role || 'user'}</span></td>
                    <td style="padding: 8px; font-size: 12px; color: #6b7280;">${u.created_at ? new Date(u.created_at).toLocaleDateString() : '-'}</td>
                </tr>`).join('') || '<tr><td colspan="4" style="padding: 20px; text-align: center; color: #9ca3af;">暂无用户</td></tr>'}
            </tbody>
        </table>
    `;
}

async function loadAdminStats() {
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
