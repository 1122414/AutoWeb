DOM_SKELETON_JS = """
(function() {
    window.__dom_result = null;
    window.__dom_status = 'pending';

    try {
        console.time("DOM_Analysis");
        
        // ================= 配置区 (Aggr. Compression) =================
        const CONFIG = {
            MAX_DEPTH: 30,             // [Reduced] 降低深度限制
            MAX_TEXT_LEN: 50,          // [Reduced] 截断长度 80 -> 50
            LIST_HEAD_COUNT: 4,        // [Reduced] 5 -> 4
            LIST_TAIL_COUNT: 1,
            VIEWPORT_RATIO: 3.0,       // [New] 视口倍率，超过 3 屏以外的内容不抓
            ATTRIBUTES_TO_KEEP: ['href', 'src', 'title', 'placeholder', 'type', 'aria-label', 'role', 'data-id', 'name', 'value']
        };
        
        const winHeight = window.innerHeight;

        // ================= 核心工具函数 =================
        
        function getXPath(element) {
            if (element.id && element.id.match(/^[a-zA-Z][a-zA-Z0-9_-]*$/)) {
                return '//*[@id="' + element.id + '"]';
            }
            if (element === document.body) return '/html/body';

            let ix = 0;
            if (!element.parentNode) return ''; 
            
            let siblings = element.parentNode.childNodes;
            for (let i = 0; i < siblings.length; i++) {
                let sibling = siblings[i];
                if (sibling === element) {
                    let parentPath = getXPath(element.parentNode);
                    return parentPath + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                }
                if (sibling.nodeType === 1 && sibling.tagName === element.tagName) {
                    ix++;
                }
            }
        }

        // [New] 视口检查
        function isInViewport(elem) {
            // body/html 始终保留
            if (elem === document.body || elem === document.documentElement) return true;
            
            const rect = elem.getBoundingClientRect();
            // 如果元素在视口上方太远，或者下方太远 (3屏外)，则忽略
            // 注意：要保留在视口上方的 Header (top < 0 但 bottom > 0)
            if (rect.bottom < 0) return false; // 滚过去了
            if (rect.top > winHeight * CONFIG.VIEWPORT_RATIO) return false; // 在很下面
            return true;
        }

        // [New] 类名降噪
        function cleanClass(cls) {
            if (!cls) return null;
            // Tailwind 检测：如果类名包含大量空格且很长
            if (cls.length > 50 && (cls.match(/ /g) || []).length > 5) {
                // 只保留看起来像关键词的
                const keywords = ['btn', 'button', 'nav', 'menu', 'item', 'list', 'card', 'title', 'input', 'form', 'active', 'selected', 'disabled', 'search', 'link'];
                const kept = cls.split(' ').filter(c => keywords.some(k => c.toLowerCase().includes(k)));
                return kept.length > 0 ? kept.join(' ') : null; // 如果没关键词，直接丢弃 Class
            }
            return cls;
        }

        function traverse(node, depth) {
            if (depth > CONFIG.MAX_DEPTH) return null;
            if (!node) return null;

            // 1. 基础过滤
            const skipTags = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH', 'HEAD', 'META', 'LINK', 'IFRAME', 'BR', 'HR', 'WBR', 'FOOTER'];
            if (skipTags.includes(node.tagName)) return null;
            if (node.nodeType !== 1) return null;

            // 2. 视口与可见性过滤
            if (node.style.display === 'none' || node.style.visibility === 'hidden' || node.getAttribute('aria-hidden') === 'true') {
                 // 保留 hidden input
                 if (!(node.tagName === 'INPUT' && node.type === 'hidden')) return null;
            }
            // [Aggressive] 视口外剪枝 (仅对主要块级元素检查，防止误杀)
            if (['DIV', 'SECTION', 'ARTICLE', 'LI'].includes(node.tagName)) {
                if (!isInViewport(node)) return null;
            }

            // 3. 数据提取
            let info = {
                t: node.tagName.toLowerCase(),
                x: getXPath(node)
            };

            if (node.id) info.id = node.id;
            
            const cleanedCls = cleanClass(node.className);
            if (cleanedCls) info.c = cleanedCls;

            CONFIG.ATTRIBUTES_TO_KEEP.forEach(attr => {
                let val = node.getAttribute(attr);
                if (val) {
                    if (val.length > 80 && (attr === 'href' || attr === 'src')) val = val.substring(0, 80) + '...';
                    info[attr] = val;
                }
            });

            // 文本提取
            let directText = "";
            node.childNodes.forEach(child => {
                if (child.nodeType === 3) {
                    let txt = child.textContent.trim();
                    if (txt) directText += txt + " ";
                }
            });
            if (directText.trim()) {
                info.txt = directText.trim().substring(0, CONFIG.MAX_TEXT_LEN);
            }

            // 4. 子节点递归与 flatten
            let children = Array.from(node.children);
            if (children.length > 0) {
                let validKids = [];
                
                // 列表采样检测
                let isList = children.length > 8;
                if (isList) {
                    let head = children.slice(0, CONFIG.LIST_HEAD_COUNT);
                    let tail = children.slice(children.length - CONFIG.LIST_TAIL_COUNT);
                    
                    head.forEach(c => {
                         let r = traverse(c, depth + 1); 
                         if(r) validKids.push(r);
                    });
                    validKids.push({ t: "skipped", count: children.length - head.length - tail.length });
                    tail.forEach(c => {
                         let r = traverse(c, depth + 1);
                         if(r) validKids.push(r);
                    });
                } else {
                    children.forEach(child => {
                        let c = traverse(child, depth + 1);
                        if (c) validKids.push(c);
                    });
                }
                
                info.kids = validKids;
                
                // [New] Wrapper Flattening (空间折叠)
                // 如果当前节点无 ID，无 Class(或已被清洗)，无属性，无文本，且只有一个子节点
                // 则直接返回子节点，跳过当前层级
                if (!info.id && !info.c && !info.txt && Object.keys(info).length <= 2 && info.kids.length === 1) {
                    // 确保不是特殊标签 (如 a, button)
                    if (!['a', 'button', 'input', 'select', 'textarea'].includes(info.t)) {
                        return info.kids[0];
                    }
                }
            }

            // 5. 垃圾节点最终清洗
            // 如果节点是空的 (无ID/Class/Txt/Attr/Kids)
            let hasAttr = Object.keys(info).some(k => CONFIG.ATTRIBUTES_TO_KEEP.includes(k));
            let isRoot = (node === document.body || node.id === 'content' || node.id === 'wrapper' || node.tagName === 'MAIN');
            
            if (!isRoot && !info.id && !info.c && !info.txt && !hasAttr && (!info.kids || info.kids.length === 0)) {
                const selfClosing = ['input', 'img', 'button', 'select', 'textarea'];
                if (!selfClosing.includes(info.t)) return null;
            }

            return info;
        }

        // ================= 执行入口 =================
        let root = document.getElementById('content') || 
                   document.getElementById('wrapper') || 
                   document.querySelector('main') || 
                   document.body;
                   
        if (root.innerText.length < 50) root = document.body;

        console.log(`🎯 压缩扫描开始: <${root.tagName} ID=${root.id}>`);
        let result = traverse(root, 0);

        if (!result) {
            // Fallback
             let fallbackText = document.body.innerText.substring(0, 1500);
             window.__dom_result = JSON.stringify({t: "body", txt: "[Structure Fail] " + fallbackText});
             window.__dom_status = 'success';
        } else {
            window.__dom_result = JSON.stringify(result);
            window.__dom_status = 'success';
        }
        
        console.timeEnd("DOM_Analysis");
        console.log("✅ 压缩完成 (Size: " + window.__dom_result.length + ")");

    } catch (e) {
        console.error("❌ 压缩崩溃:", e);
        window.__dom_result = JSON.stringify({error: e.toString()});
        window.__dom_status = 'error';
    }
})();
"""