# Changelog

## 1.0.0 (2024-01-30)


### ⚠ BREAKING CHANGES

* change main endpoint
* create separate routes for tools

### Features

* add `cpu_MHz` ([ce1d74a](https://github.com/ai4os/ai4-papi/commit/ce1d74a49fefcbed82223369aacd2285670c974b))
* add active endpoints ([35f53f0](https://github.com/ai4os/ai4-papi/commit/35f53f0025adff3d6ca45da974d376c374c0c1fb))
* add checks for JWT scopes ([c71e918](https://github.com/ai4os/ai4-papi/commit/c71e918f7b51afeaa63b09b4d05c2d732ad504eb))
* add datacenter to deployment info ([7773a02](https://github.com/ai4os/ai4-papi/commit/7773a0257abc36adc6713af04d050047d5aa8a22))
* add federated token as env variable ([1646bcc](https://github.com/ai4os/ai4-papi/commit/1646bcc4db6b55cec8a59cb5a4c8acb6817c3694))
* add monitor port to fedserver tool to enable ttyd ([c849fe4](https://github.com/ai4os/ai4-papi/commit/c849fe4d552ffb5782fddea3e36fdaf7f3c784b0))
* add name and email to Nomad jobs ([8743e31](https://github.com/ai4os/ai4-papi/commit/8743e31943d341a5ff51f865bb2d905a178cf784))
* add release-please support ([16f17c3](https://github.com/ai4os/ai4-papi/commit/16f17c39692df575a19e5f090d4f83ba9d4854ed))
* add storage task ([0efa70f](https://github.com/ai4os/ai4-papi/commit/0efa70ff9cdea6724a44012ce854d95b5f91e021))
* allow SSL in deployments ([6a3857d](https://github.com/ai4os/ai4-papi/commit/6a3857d04fe8c5db6bed481fdfc50416b0317285))
* auto-discover available gpu models ([d31e3f5](https://github.com/ai4os/ai4-papi/commit/d31e3f5cc9d7cb379f60454be727171def456093))
* create separate routes for tools ([6fd0fc5](https://github.com/ai4os/ai4-papi/commit/6fd0fc5f9532daabf794635145b6ec4506d22f23))
* deployment creation uses `string.Template` ([ed79f27](https://github.com/ai4os/ai4-papi/commit/ed79f272d9692b8ab77326e3ec3a98af905366fe))
* disable custom domain, leave custom host ([1771a31](https://github.com/ai4os/ai4-papi/commit/1771a314a4d3c8b61e66251f7ca794a1ac5ac191))
* implement total GPUs quota per user ([55baab8](https://github.com/ai4os/ai4-papi/commit/55baab853e314b7e78c9dd7b16ddfd8fbae68ca5))
* remove proxy + let's encrypt ([d7c0ef0](https://github.com/ai4os/ai4-papi/commit/d7c0ef080941afa3a3c8f850fed70435097b6a27))
* replace `deepaas` with `api` in Nomad jobs ([da764eb](https://github.com/ai4os/ai4-papi/commit/da764eb4787bec40028e295db86ee3031e5ad36d))
* return requested resources for queued jobs ([7037e4e](https://github.com/ai4os/ai4-papi/commit/7037e4ea393269f1cc923d5f94a0014d2f1b33d1))
* update to new Nextcloud instance ([e2c11e7](https://github.com/ai4os/ai4-papi/commit/e2c11e7c412d9bf9ecea9c688595ff311fa88b44))


### Bug Fixes

* `gpu_num` for multi-gpu deployments ([4b39af1](https://github.com/ai4os/ai4-papi/commit/4b39af1087cc90c2281d5f017dfd83eb35da8da9))
* `module_name` parsing ([be3e502](https://github.com/ai4os/ai4-papi/commit/be3e502d836043de407e6fc55eb617a4ac8df284))
* add back `/ui` to `api` endpoint ([205daa7](https://github.com/ai4os/ai4-papi/commit/205daa7a596a539df095ce9d902a90eb787eb86f))
* allow requests from ai4os-proxy ([f09a07d](https://github.com/ai4os/ai4-papi/commit/f09a07dec3c11507c3cd170ef446328db27c7d24))
* avoid restarting jobs when network is temporarily lost ([49bc1e6](https://github.com/ai4os/ai4-papi/commit/49bc1e620770cc54f7945e5741ff1a45097bf9bc))
* better error catching ([9894fbc](https://github.com/ai4os/ai4-papi/commit/9894fbc8a669b79dba97fa7db3be925eb28776da))
* better hardware limit ([8d9a30a](https://github.com/ai4os/ai4-papi/commit/8d9a30a02fcf70640e5f310cae4ebd22ddfaf35f))
* change lowercase ([de8630d](https://github.com/ai4os/ai4-papi/commit/de8630d2ba0b0f45e26e462e015a9fea5bbdc537))
* check in `gpu_model` affinity is empty ([2fabe18](https://github.com/ai4os/ai4-papi/commit/2fabe18ed20f503c103dc5e014acd8439ccd6111))
* comment `cpu_MHz` ([d58f7d7](https://github.com/ai4os/ai4-papi/commit/d58f7d768083b949cbffc91d79989f59570a363a))
* disable Nomad storage tasks if credentials not provided ([de3783d](https://github.com/ai4os/ai4-papi/commit/de3783d3d58820fc38678e49b8d271ddb9469d75))
* docker tags pagination ([929325c](https://github.com/ai4os/ai4-papi/commit/929325c111b51c836fddf2e84fc8ab2b65e9f082))
* endpoints retrieval ([7aeea2b](https://github.com/ai4os/ai4-papi/commit/7aeea2bf1e7f461a5431ecc270dac5ff7f68e0b4))
* GPU modelnames ([4e37c55](https://github.com/ai4os/ai4-papi/commit/4e37c559cc61fc498850fb7a975342e2a1163b5d))
* ignore user disk ([c2ba8aa](https://github.com/ai4os/ai4-papi/commit/c2ba8aa7c14d738545a9a220c2fe9428ff4a6b0a))
* increase shared memory limit in Docker ([c5949cd](https://github.com/ai4os/ai4-papi/commit/c5949cd653bf82f7d7cb4a6c63424e4fb293b5cb))
* parse modules from .gitmodules file instead of YAML ([b38f11b](https://github.com/ai4os/ai4-papi/commit/b38f11b5d7fe5d1be7593e93d87b8bd6a111c5cf))
* pyyaml version ([e69422c](https://github.com/ai4os/ai4-papi/commit/e69422ce633553a7bb9aa26d5a90f24ad4db1a6c))
* remove gpu_model constraint if model is not specified ([3d11fdb](https://github.com/ai4os/ai4-papi/commit/3d11fdb4a2ed7d1860674491bcf14d77edc97c83))
* set `cpu_num` to cores, not to MHz ([da9186b](https://github.com/ai4os/ai4-papi/commit/da9186bee8fe0d5acbb7b706944feeca26c8c3c9))
* update Docker compose files ([487be86](https://github.com/ai4os/ai4-papi/commit/487be86aa018685f2c86b08469a01ac6f7b04270))
* use constraint instead of affinity for GPU models ([897ac78](https://github.com/ai4os/ai4-papi/commit/897ac780ab4807eae4bd4e96e5bd02f60a3f82fb))


### Performance Improvements

* improve `check_domain` ([643aed6](https://github.com/ai4os/ai4-papi/commit/643aed6de66aa291c5454be44ef67bdad8b984b1))
* improve job retrieval with Nomad filters ([a21272d](https://github.com/ai4os/ai4-papi/commit/a21272dfa744601ce105444136a73a4334511689))


### Code Refactoring

* change main endpoint ([2558b4b](https://github.com/ai4os/ai4-papi/commit/2558b4bd1c72f4f07ea5741dc7011119f71c3171))
