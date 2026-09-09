const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs'),path=require('path'),vm=require('vm');
const ts=require('../node_modules/typescript');
const {harness}=require('./lib/offline-tsx-harness.cjs');
const modulePort={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/adapters/SessionAccess.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{module:modulePort,exports:modulePort.exports});
const access=modulePort.exports;
function screen(file,role,pathname='/reports/private') {
  const state={session:{product_role:role},loading:false,error:null,reload(){}};
  const h=harness(file,{imports:{
    '../adapters':{useSessionAccess:()=>state,isMockData:false},
    '../adapters/SessionAccess':access,
    '../pages/Layout':{__esModule:true,default:'BusinessLayout'},
    'react-router-dom':{Navigate:'Navigate',useLocation:()=>({pathname}),useNavigate:()=>()=>{}},
  }});
  h.render();return {h,state};
}
test('non-admin roles cannot render the console or its child data pages',()=>{
  for(const role of ['client_user','provider_consultant','provider_reviewer','technical_admin','unconfigured']) {
    const {h}=screen('shells/ConsoleLayout.tsx',role);
    assert.equal(h.tree().type,'Navigate');assert.equal(h.tree().props.to,access.homePathFor(role));h.unmount();
  }
});
test('provider and technical identities cannot render customer portal children',()=>{
  for(const role of ['provider_admin','provider_consultant','provider_reviewer','technical_admin','unconfigured']) {
    const {h}=screen('shells/PortalLayout.tsx',role);
    assert.equal(h.tree().type,'Navigate');assert.equal(h.tree().props.to,access.homePathFor(role));h.unmount();
  }
});
test('denied legacy destinations return to an accessible home without a redirect loop',()=>{
  for(const role of ['provider_consultant','provider_reviewer','technical_admin']) {
    const denied=screen('shells/LegacyProviderGate.tsx',role);
    assert.equal(denied.h.tree().type,'Navigate');
    const landing=screen('shells/LegacyProviderGate.tsx',role,denied.h.tree().props.to);
    assert.equal(landing.h.tree().type,'BusinessLayout');denied.h.unmount();landing.h.unmount();
  }
});
test('session refresh removes the previous role layout while loading or failing',()=>{
  const {h,state}=screen('shells/LegacyProviderGate.tsx','provider_admin');
  assert.equal(h.tree().type,'BusinessLayout');
  state.loading=true;h.render();assert.equal(h.tree().type,'Spin');
  state.loading=false;state.error=new Error('session unavailable');h.render();assert.notEqual(h.tree().type,'BusinessLayout');
  state.error=null;state.session={product_role:'client_user'};h.render();assert.equal(h.tree().type,'Navigate');assert.equal(h.tree().props.to,'/portal');h.unmount();
});
