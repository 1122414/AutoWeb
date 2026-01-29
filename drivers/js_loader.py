DOM_SKELETON_JS = """
(function() {
    window.__dom_result = null;
    window.__dom_status = 'pending';

    try {
        console.time("DOM_Analysis");
        
        // ================= 配置区 (Balanced Compression) =================
        const CONFIG = {
            MAX_DEPTH: 50,             // [Relaxed] 30 -> 50
            MAX_TEXT_LEN: 200,         // [Relaxed] 50 -> 200 (保留更多描述)
            LIST_HEAD_COUNT: 10,       // [Relaxed] 4 -> 10 (列表多看点)
            LIST_TAIL_COUNT: 2,        // [Relaxed] 1 -> 2
            VIEWPORT_RATIO: 10.0,      // [Relaxed] 3.0 -> 10.0 (基本覆盖长页面)
            ATTRIBUTES_TO_KEEP: ['href', 'src', 'title', 'placeholder', 'type', 'aria-label', 'role', 'data-id', 'name', 'value', 'target'] // [Added] target
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

        // [Relaxed] 视口检查 (更加宽容)
        function isInViewport(elem) {
            // 关键元素始终保留
            if (['INPUT', 'BUTTON', 'A', 'FORM', 'IMG'].includes(elem.tagName)) return true;
            if (elem === document.body || elem === document.documentElement) return true;
            
            const rect = elem.getBoundingClientRect();
            
            // 只有当元素完全滚出上方很远 (>2屏) 时才剪裁
            if (rect.bottom < -winHeight * 2) return false; 
            
            // 下方保留 10 屏
            if (rect.top > winHeight * CONFIG.VIEWPORT_RATIO) return false; 
            
            return true;
        }

        // [Improved] 类名降噪
        function cleanClass(cls) {
            if (!cls) return null;
            // Tailwind/原子类 CSS 检测
            if (cls.length > 50 && (cls.match(/ /g) || []).length > 4) {
                const keywords = ['btn', 'nav', 'menu', 'item', 'list', 'card', 'title', 'input', 'form', 'active', 'selected', 'search', 'link', 'banner', 'main', 'footer', 'header'];
                const kept = cls.split(' ').filter(c => keywords.some(k => c.toLowerCase().includes(k)));
                return kept.length > 0 ? kept.join(' ') : null;
            }
            return cls;
        }

        function traverse(node, depth) {
            if (depth > CONFIG.MAX_DEPTH) return null;
            if (!node) return null;

            // 1. 基础过滤
            const skipTags = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH', 'HEAD', 'META', 'LINK', 'IFRAME', 'BR', 'HR', 'WBR'];
            if (skipTags.includes(node.tagName)) return null;
            if (node.nodeType !== 1) return null;

            // 2. 视口与可见性过滤
            const style = window.getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden') {
                 // 保留 hidden input (承载数据)
                 if (!(node.tagName === 'INPUT' && node.type === 'hidden')) return null;
            }
            if (node.getAttribute('aria-hidden') === 'true') {
                 // Aria-hidden 有时只是装饰性隐藏，还是稍微检查下
                 if (!['DIV', 'SPAN'].includes(node.tagName)) return null;
            }
            
            // 视口剪枝 (仅对布局容器粗剪，叶子节点细剪)
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
                    if (val.length > 100 && (attr === 'href' || attr === 'src')) val = val.substring(0, 100) + '...';
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
                info.txt = directText.trim();
                if (info.txt.length > CONFIG.MAX_TEXT_LEN) {
                    info.txt = info.txt.substring(0, CONFIG.MAX_TEXT_LEN) + "...";
                }
            }

            // 4. 子节点递归与 flatten
            let children = Array.from(node.children);
            if (children.length > 0) {
                let validKids = [];
                
                // 列表采样检测
                let isList = children.length > 15; // 提高阈值，少折叠
                if (isList) {
                    let head = children.slice(0, CONFIG.LIST_HEAD_COUNT);
                    let tail = children.slice(children.length - CONFIG.LIST_TAIL_COUNT);
                    
                    head.forEach(c => {
                         let r = traverse(c, depth + 1); 
                         if(r) validKids.push(r);
                    });
                    
                    let skippedCount = children.length - head.length - tail.length;
                    if (skippedCount > 0) {
                        validKids.push({ t: "skipped", count: skippedCount });
                    }
                    
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
                
                if (validKids.length > 0) info.kids = validKids;
                
                // [Wrapper Flattening] 仅对无意义、无属性的纯包裹层进行折叠
                // 必须非常谨慎，因为 XPath 依赖层级
                // 此处取消 Flattening 以保证 XPath 绝对准确性与 Agent 理解
            }

            // 5. 垃圾节点最终清洗 (Empty Node Filter)
            // 如果节点是空的 (无ID/Class/Txt/Attr/Kids)
            // 保留主要布局标签以免破坏结构
            let hasAttr = Object.keys(info).some(k => CONFIG.ATTRIBUTES_TO_KEEP.includes(k));
            let isStructural = ['DIV', 'MAIN', 'SECTION', 'ARTICLE', 'HEADER', 'FOOTER', 'NAV', 'UL', 'OL', 'TABLE', 'TR', 'TD'].includes(node.tagName);
            
            if (!info.id && !info.c && !info.txt && !hasAttr && (!info.kids || info.kids.length === 0)) {
                if (!isStructural) return null; 
            }

            return info;
        }

        // ================= 执行入口 =================
        // 优先全量扫描，只有当 DOM 确实巨大 (预计) 时才收缩 Scope
        // 实际上 LLM 需要全局视野，我们尽量用 body
        let root = document.body;
        
        console.log(`🎯 全量扫描开始: <${root.tagName}>`);
        let result = traverse(root, 0);

        if (!result) {
             let fallbackText = document.body.innerText.substring(0, 2000);
             window.__dom_result = JSON.stringify({t: "body", txt: "[Structure Fail] " + fallbackText});
             window.__dom_status = 'success';
        } else {
            window.__dom_result = JSON.stringify(result);
            window.__dom_status = 'success';
        }
        
        console.timeEnd("DOM_Analysis");
        console.log("✅ 完成 (Size: " + window.__dom_result.length + ")");

    } catch (e) {
        console.error("❌ 压缩崩溃:", e);
        window.__dom_result = JSON.stringify({error: e.toString()});
        window.__dom_status = 'error';
    }
})();
"""