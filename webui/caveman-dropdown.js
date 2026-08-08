(function(){
console.log("[caveman]"+new Error().stack.split("\n")[1]+" injector running");
var LEVELS=[
{v:"off",l:"Off",d:"Default - no compression"},
{v:"lite",l:"Lite",d:"Articles, fragments OK"},
{v:"full",l:"Full",d:"Drop articles, fragments OK"},
{v:"ultra",l:"Ultra",d:"Article drop, fragments gone"},
{v:"wenyan-lite",l:"Wenyan Lite",d:"Wenyan compressed, light"},
{v:"wenyan-full",l:"Wenyan Full",d:"Wenyan + full level"},
{v:"wenyan-ultra",l:"Wenyan Ultra",d:"Wenyan + ultra level"},
];
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
return fetch("/api/plugins/caveman/caveman_state",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){
return r.json();
});
}
function closeMenu(){if(window.__cavemanMenu)window.__cavemanMenu.style.display="none";}
function openMenu(){if(window.__cavemanMenu)window.__cavemanMenu.style.display="block";}
function refresh(){
var cid=pickChatId();if(cid)window.__cavemanChatId=cid;
api("get").then(function(r){
var lvl=(r&&r.level)||"off";
if(window.__cavemanBtn)window.__cavemanBtn.textContent="Caveman: "+lvl;
}).catch(function(){});
}
function build(){
if(window.__cavemanBtn)return;
var host=document.querySelector("x-extension[id='chat-input-bottom-actions-end']");
if(!host){
var slots=document.querySelectorAll("x-extension");
for(var i=0;i<slots.length;i++){
var id=slots[i].id||"";
if(id.toLowerCase().indexOf("bottom-actions-end")!==-1){host=slots[i];break;}
}
}
if(!host)return false;
var div=document.createElement("div");
div.style.display="inline-flex";
div.style.alignItems="center";
div.style.marginLeft="auto";
div.style.position="relative";
var btn=document.createElement("button");
btn.type="button";
btn.textContent="Caveman: off";
btn.style.background="transparent";
btn.style.border="none";
btn.style.borderRadius="5px";
btn.style.padding="4px 10px";
btn.style.font="inherit";
btn.style.color="inherit";
btn.style.cursor="pointer";
btn.style.opacity="0.85";
btn.addEventListener("mouseenter",function(){btn.style.opacity="1";});
btn.addEventListener("mouseleave",function(){btn.style.opacity="0.85";});
btn.addEventListener("click",function(e){
e.preventDefault();e.stopPropagation();
if(window.__cavemanMenu.style.display==="block")closeMenu();else openMenu();
});
div.appendChild(btn);
var menu=document.createElement("div");
menu.style.display="none";
menu.style.position="absolute";
menu.style.bottom="100%";
menu.style.right="0";
menu.style.marginBottom="6px";
menu.style.background="var(--color-background,#1e1e1e)";
menu.style.border="1px solid var(--color-border,#444)";
menu.style.borderRadius="8px";
menu.style.padding="6px 0";
menu.style.minWidth="200px";
menu.style.boxShadow="0 4px 16px rgba(0,0,0,0.4)";
menu.style.zIndex="9999";
menu.addEventListener("click",function(e){e.stopPropagation();});
for(var j=0;j<LEVELS.length;j++){
(function(L){
var it=document.createElement("button");
it.type="button";
it.style.display="block";
it.style.width="100%";
it.style.textAlign="left";
it.style.padding="8px 14px";
it.style.background="transparent";
it.style.border="none";
it.style.color="inherit";
it.style.font="inherit";
it.style.cursor="pointer";
it.innerHTML='<div style="font-weight:600">'+L.l+'</div><div style="font-size:11px;opacity:0.7">'+L.d+'</div>';
it.addEventListener("mouseenter",function(){it.style.background="var(--color-background-hover,#333)";});
it.addEventListener("mouseleave",function(){it.style.background="transparent";});
it.addEventListener("click",function(e){
e.preventDefault();e.stopPropagation();
var cid=pickChatId();if(!cid){console.log("[caveman] no chat id");return;}
window.__cavemanChatId=cid;
api("set",L.v).then(function(r){
btn.textContent="Caveman: "+L.v;
closeMenu();
console.log("[caveman] set to "+L.v);
}).catch(function(err){
console.log("[caveman] set failed",err);
btn.textContent="Caveman: ERROR";
setTimeout(function(){btn.textContent="Caveman: "+L.v;},1500);
});
});
menu.appendChild(it);
})(LEVELS[j]);
}
div.appendChild(menu);
host.appendChild(div);
window.__cavemanBtn=btn;
window.__cavemanMenu=menu;
console.log("[caveman] dropdown built");
return true;
}
function tick(){
if(!window.__cavemanBtn)build();
if(window.__cavemanBtn)refresh();
}
if(document.readyState==="loading"){
document.addEventListener("DOMContentLoaded",tick);
}else tick();
setInterval(function(){if(document.visibilityState==="visible")tick();},60000);
document.addEventListener("click",function(e){
if(window.__cavemanMenu&&!e.target.closest("button"))closeMenu();
});
})();
