'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {isHealth,allowNavigation,externalURL}=require('../desktop/service.cjs');
test('only the local application may navigate in the privileged desktop window',()=>{
 assert(allowNavigation('http://127.0.0.1:8765/#settings'));
 for(const url of ['https://evil.example','http://127.0.0.1:8765.evil.example','http://127.0.0.1:8765/api/state','file:///etc/passwd','javascript:alert(1)'])assert(!allowNavigation(url));
});
test('external links reject executable schemes and credentials',()=>{
 assert(externalURL('https://github.com/LzumiTulle/QQmanager'));
 for(const url of ['file:///tmp/app','javascript:1','mailto:x@y.z','https://user:password@example.com'])assert(!externalURL(url));
});
test('do not mistake another local server for QQmanager',()=>{
 assert(isHealth({app:'QQmanager',protocol:1}));assert(!isHealth({app:'other',protocol:1}));assert(!isHealth({app:'QQmanager',protocol:99}));
});
