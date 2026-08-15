const solc = require('/data/data/com.termux/files/home/OS-AI-Backend/contracts/node_modules/solc');
const fs = require('fs');
const source = fs.readFileSync(__dirname + '/CloseStaking.sol', 'utf8');
const input = {
  language: 'Solidity',
  sources: { 'CloseStaking.sol': { content: source } },
  settings: { outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } }, optimizer: { enabled: true, runs: 200 } }
};
const output = JSON.parse(solc.compile(JSON.stringify(input)));
if (output.errors) {
  let hasError = false;
  for (const e of output.errors) { console.log(e.severity.toUpperCase()+':', e.formattedMessage); if (e.severity === 'error') hasError = true; }
  if (hasError) process.exit(1);
}
const c = output.contracts['CloseStaking.sol']['CloseStaking'];
fs.writeFileSync(__dirname + '/CloseStaking.abi.json', JSON.stringify(c.abi, null, 2));
fs.writeFileSync(__dirname + '/CloseStaking.bytecode.txt', c.evm.bytecode.object);
console.log('Compiled OK. Bytecode size:', c.evm.bytecode.object.length / 2, 'bytes');
