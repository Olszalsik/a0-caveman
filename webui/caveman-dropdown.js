(function(){
console.log("[caveman] injector running");
var LEVELS=[
{v:"off",l:"Off",d:"Default - no compression"},
{v:"lite",l:"Lite",d:"Articles, fragments OK"},
{v:"full",l:"Full",d:"Drop articles, fragments OK"},
{v:"ultra",l:"Ultra",d:"Article drop, fragments gone"},
{v:"wenyan-lite",l:"Wenyan Lite",d:"Wenyan compressed, light"},
{v:"wenyan-full",l:"Wenyan Full",d:"Wenyan + full level"},
{v:"wenyan-ultra",l:"Wenyan Ultra",d:"Wenyan + ultra level"},
];
var ICON='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="5" r="2"/><path d="M6 8 C 5 10, 5 12, 6 14 L 7 17 L 7 21 L 8 21 L 8 18 L 10 18 L 10 21 L 11 21 L 11 17 L 12 14 L 13 12 L 16 9 L 15 8 L 13 9 L 11 10.5 L 9 10.5 C 8 9.5, 7.5 9, 6 8 Z"/><line x1="15" y1="11" x2="20" y2="5"/><polyline points="17 5, 20 5, 20 8"/></svg>';
function pickChatId(){
try{
if(window.Alpine&&window.Alpine.store){
var stores=["chats","chat","chatStore","selectedChat","store"];
for(var i=0;i<stores.length;i++){
var s=window.Alpine.store(stores[i]);
if(!s)continue;
if(s.selected)return s.selected.id||s.selected.context_id||String(s.selected);
if(s.current)return s.current.id||s.current.context_id||String(s.current);
if(s.id)return s.id;
if(s.context_id)return s.context_id;
}
}
var u=new URL(location.href);
return u.searchParams.get("ctxid")||u.searchParams.get("chat_id")||u.searchParams.get("chat");
}catch(e){return null;}
}
function api(action,value){
var body={action:action,chat_id:window.__cavemanChatId||null};
if(value!==undefined)body.level=value;
return fetch("/api/plugins/caveman/caveman_state",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){return r.json();});
}
function closeMenu(){if(window.__cavemanMenu)window.__cavemanMenu.style.display="none";}
function openMenu(){if(window.__cavemanMenu)window.__cavemanMenu.style.display="block";}
function refresh(){
var cid=pickChatId();if(cid)window.__cavemanChatId=cid;
api("get").then(function(r){
var lvl=(r&&r.level)||"off";
window.__cavemanLastLevel=lvl;
if(window.__cavemanBtnLabel)window.__cavemanBtnLabel.textContent="Caveman: "+lvl;
}).catch(function(){});
}
function findHost(){
var host=document.querySelector("x-extension[id='chat-input-bottom-actions-end']");
if(host)return host;
var all=document.querySelectorAll("x-extension");
for(var i=0;i<all.length;i++){
var id=all[i].id||"";
if(id.indexOf("bottom-actions-end")!==-1)return all[i];
}
return null;
}
function build(){
if(window.__cavemanBtn)return true;
var host=findHost();
if(!host)return false;
host.innerHTML="";
host.style.position="relative";
var btn=document.createElement("button");
btn.type="button";
btn.className="text-button";
btn.id="caveman-btn";
btn.style.fontSize="0.6rem";
btn.style.display="inline-flex";
btn.style.alignItems="center";
btn.style.gap="4px";
var ico=document.createElement("span");
ico.innerHTML=ICON;
ico.style.display="inline-flex";
ico.style.width="14px";
ico.style.height="14px";
btn.appendChild(ico);
var label=document.createElement("span");
label.textContent="Caveman: off";
label.style.fontSize="0.6rem";
btn.appendChild(label);
host.appendChild(btn);
var menu=document.createElement("div");
menu.style.display="none";
menu.style.position="absolute";
menu.style.bottom="calc(100% + 6px)";
menu.style.right="0";
menu.style.background="var(--color-background,#1e1e1e)";
menu.style.border="1px solid var(--color-border,#444)";
menu.style.borderRadius="8px";
menu.style.padding="6px 0";
menu.style.minWidth="220px";
menu.style.boxShadow="0 4px 16px rgba(0,0,0,0.4)";
menu.style.zIndex="9999";
host.appendChild(menu);
window.__cavemanMenu=menu;
function renderMenu(){
menu.innerHTML="";
for(var j=0;j<LEVELS.length;j++){
(function(L){
var it=document.createElement("button");
it.type="button";
it.style.display="block";
it.style.width="100%";
it.style.textAlign="left";
it.style.padding="6px 14px";
it.style.background="transparent";
it.style.border="none";
it.style.color="inherit";
it.style.font="inherit";
it.style.fontSize="0.6rem";
it.style.cursor="pointer";
var row=document.createElement("div");
row.style.fontWeight="600";
row.textContent=L.l+(L.v===window.__cavemanLastLevel?" \u2713":"");
var desc=document.createElement("div");
desc.style.fontSize="0.55rem";
desc.style.opacity="0.7";
desc.textContent=L.d;
it.appendChild(row);
it.appendChild(desc);
it.addEventListener("mouseenter",function(){it.style.background="var(--color-background-hover,#333)";});
it.addEventListener("mouseleave",function(){it.style.background="transparent";});
it.addEventListener("mousedown",function(e){e.stopPropagation();});
it.addEventListener("click",function(e){
e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();
var cid=pickChatId();
if(!cid){console.log("[caveman] no chat id");return;}
window.__cavemanChatId=cid;
window.__cavemanLastLevel=L.v;
label.textContent="Caveman: "+L.v;
api("set",L.v).then(function(r){
closeMenu();
renderMenu();
console.log("[caveman] set to "+L.v);
}).catch(function(err){
console.log("[caveman] set failed",err);
label.textContent="Caveman: ERROR";
setTimeout(function(){label.textContent="Caveman: "+window.__cavemanLastLevel;},1500);
});
});
menu.appendChild(it);
})(LEVELS[j]);
}
}
renderMenu();
btn.addEventListener("click",function(e){
e.preventDefault();e.stopPropagation();
if(menu.style.display==="block")closeMenu();else openMenu();
});
btn.addEventListener("mousedown",function(e){e.stopPropagation();});
window.__cavemanBtn=btn;
window.__cavemanBtnLabel=label;
window.__cavemanLastLevel="off";
console.log("[caveman] dropdown built");
return true;
}
function tick(){
var built=build();
if(built)refresh();
}
if(document.readyState==="loading"){
document.addEventListener("DOMContentLoaded",tick);
}else tick();
setInterval(tick,2000);
document.addEventListener("click",function(e){
if(window.__cavemanMenu&&!e.target.closest("#caveman-btn"))closeMenu();
});
})();
