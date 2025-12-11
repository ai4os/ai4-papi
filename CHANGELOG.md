# Changelog

## [1.3.0](https://github.com/ai4os/ai4-papi/compare/v1.2.0...v1.3.0) (2025-08-13)


### Features

* adapt to new node `type` metadata ([9d77872](https://github.com/ai4os/ai4-papi/commit/9d778727321b4726cd5a5055a88d76fbd20e501b))
* add ai4llm proxy endpoint ([#109](https://github.com/ai4os/ai4-papi/issues/109)) ([a91ddb1](https://github.com/ai4os/ai4-papi/commit/a91ddb1647d8a7334991614d5ed69297c8781c18))
* add async support for OSCAR ([#72](https://github.com/ai4os/ai4-papi/issues/72)) ([387a21f](https://github.com/ai4os/ai4-papi/commit/387a21fd0d6abfb26cc384b6a945393cb0084997))
* add code carbon variable to fedserver ([#97](https://github.com/ai4os/ai4-papi/issues/97)) ([ead0838](https://github.com/ai4os/ai4-papi/commit/ead08385fb68bf5167f2fddc493c65848667f3fe))
* add current PAPI branch+commit in the docs' description ([c5bca42](https://github.com/ai4os/ai4-papi/commit/c5bca42e326d0d01288c9e7895ff6d23179a2d16))
* add custom username to llm tool ([#105](https://github.com/ai4os/ai4-papi/issues/105)) ([46a2a0e](https://github.com/ai4os/ai4-papi/commit/46a2a0eb45baec376c0e7772dbde1f5bddedb2f1))
* add email notification when slow deployment times ([#60](https://github.com/ai4os/ai4-papi/issues/60)) ([22b066c](https://github.com/ai4os/ai4-papi/commit/22b066c253cc09cb2992bfe875134a2d4e369fbf))
* add FedAvgOpt strategy ([#127](https://github.com/ai4os/ai4-papi/issues/127)) ([975fce5](https://github.com/ai4os/ai4-papi/commit/975fce5004b2ae29d5cf78584b2aa6d72a55a9eb))
* add item `id` to the metadata dict ([d535086](https://github.com/ai4os/ai4-papi/commit/d5350860b5c1fa55fc5bc58943754aa140de57b5))
* add LLM catalog ([#103](https://github.com/ai4os/ai4-papi/issues/103)) ([53618f0](https://github.com/ai4os/ai4-papi/commit/53618f00c444069d48efd57eb049adebb07ec002))
* add LLM tool ([#89](https://github.com/ai4os/ai4-papi/issues/89)) ([f39d2c4](https://github.com/ai4os/ai4-papi/commit/f39d2c4c39907d8afdcc9f17792a0ceaaf3031d2))
* add metric privacy variable to federated server ([#95](https://github.com/ai4os/ai4-papi/issues/95)) ([d06d0a6](https://github.com/ai4os/ai4-papi/commit/d06d0a6846b91855bd9d81359f8c47cbe413257e))
* add MinIO info to service detail ([#125](https://github.com/ai4os/ai4-papi/issues/125)) ([77ef5d4](https://github.com/ai4os/ai4-papi/commit/77ef5d433b1124c6efb3d9b69da76dc2c3542138))
* add support for CVAT tool ([#73](https://github.com/ai4os/ai4-papi/issues/73)) ([8cc62ab](https://github.com/ai4os/ai4-papi/commit/8cc62ab4c6ad18d6692a53e49d9f6061ad6dde9c))
* add support for OSCAR services ([#25](https://github.com/ai4os/ai4-papi/issues/25)) ([5954569](https://github.com/ai4os/ai4-papi/commit/5954569dc7abcf128a466205cd6cd0188bc1ad6c))
* add UI to Nomad inference endpoints ([#76](https://github.com/ai4os/ai4-papi/issues/76)) ([429c726](https://github.com/ai4os/ai4-papi/commit/429c726e3f9aba7841faa75403e84e603af3cfa3))
* AI4OS Development Environment as a tool ([#111](https://github.com/ai4os/ai4-papi/issues/111)) ([7a0497d](https://github.com/ai4os/ai4-papi/commit/7a0497db1006c0de823583a7282ed596ee42fca8))
* allow also URLs for dataset downloads ([#65](https://github.com/ai4os/ai4-papi/issues/65)) ([17563f5](https://github.com/ai4os/ai4-papi/commit/17563f590c3990c0fa769db3bfc9e2548ae01c93))
* allow listing/deleting try-me ([#68](https://github.com/ai4os/ai4-papi/issues/68)) ([6fa977a](https://github.com/ai4os/ai4-papi/commit/6fa977a7bb28ef04cf0e22d436328fae9030b335))
* allow to deploy as Nomad job ([#101](https://github.com/ai4os/ai4-papi/issues/101)) ([3501be5](https://github.com/ai4os/ai4-papi/commit/3501be538cd0d05560226279f290ef2480d4aa6f))
* be conservative with tryme resource cap ([4026f7a](https://github.com/ai4os/ai4-papi/commit/4026f7af1000a6caf0af291b0f30715a4dab7448))
* **catalog:** point FL server to new repo ([d5075cc](https://github.com/ai4os/ai4-papi/commit/d5075cc008640ac54013936eacca7453af1921aa))
* config thinking for deepseek ([#98](https://github.com/ai4os/ai4-papi/issues/98)) ([3bbbbac](https://github.com/ai4os/ai4-papi/commit/3bbbbaca0d49583dcdd031b7efb9fffeab26a3cf))
* disable some code sections if we are doing local development ([a8b81a9](https://github.com/ai4os/ai4-papi/commit/a8b81a9aa152c7f9c1585f5e35c9a92c76a1fa73))
* disallow usage of GPUs to CPU-only jobs ([#96](https://github.com/ai4os/ai4-papi/issues/96)) ([39383ab](https://github.com/ai4os/ai4-papi/commit/39383abe86b3cdb3b6ee4b97668348e987ebe7b0))
* enforce docker image to belong to either `deephdc` or `ai4oshub` DockerHub orgs (or our Harbor) ([78fe123](https://github.com/ai4os/ai4-papi/commit/78fe123d0a378b2e12feb6999b9051a4dc3e4600))
* enforce minimum resources for user's metadata  ([#110](https://github.com/ai4os/ai4-papi/issues/110)) ([9158100](https://github.com/ai4os/ai4-papi/commit/9158100ef4ac1e83b9d2405e0601873a98a79bac))
* improve imports ([45475de](https://github.com/ai4os/ai4-papi/commit/45475de6d0f80eb9e540961f228f57a46e2a8dfc))
* increase metadata cache expiration ([88fc55d](https://github.com/ai4os/ai4-papi/commit/88fc55d82ee9e5d46cae08e3215bd6a120f2d245))
* increase RAM for fedserver ([028b28e](https://github.com/ai4os/ai4-papi/commit/028b28eaf4e9fe5f4f102832ec939146065b532c))
* increase snapshot quota to 15GB ([aedc058](https://github.com/ai4os/ai4-papi/commit/aedc058106c6270e85fb36ce253e53e034712013))
* inject MLflow credentials in Nomad jobs ([a759d4d](https://github.com/ai4os/ai4-papi/commit/a759d4d0efdd811974641a43f17d4def47a3d992))
* **metadata:** support MLDCAT-AP profile ([#108](https://github.com/ai4os/ai4-papi/issues/108)) ([e0fbfa8](https://github.com/ai4os/ai4-papi/commit/e0fbfa8e62fe7e34be4ce4cdd87ba1d0ebe93e91))
* migrate to keycloak ([#117](https://github.com/ai4os/ai4-papi/issues/117)) ([38e5f98](https://github.com/ai4os/ai4-papi/commit/38e5f9870daa06d4a0ffafc5dacdf907f1d7d9ef))
* migrate to new metadata ([#63](https://github.com/ai4os/ai4-papi/issues/63)) ([14146a5](https://github.com/ai4os/ai4-papi/commit/14146a5bac0487058189945edd605291d525de43))
* overwrite some metadata with Github info ([6964cbe](https://github.com/ai4os/ai4-papi/commit/6964cbe150b4b13acc3efaa7418a7fc4c15acb36))
* remove `custom domain` field from configuration forms ([#67](https://github.com/ai4os/ai4-papi/issues/67)) ([9a23f87](https://github.com/ai4os/ai4-papi/commit/9a23f8792075455f3042e4cd70cf7cc6ec81adf1))
* remove deprecated tags method ([e9f87aa](https://github.com/ai4os/ai4-papi/commit/e9f87aa77253f8c600b788f4060bbe3cfbe887b3))
* remove trailing slash in endpoints ([#64](https://github.com/ai4os/ai4-papi/issues/64)) ([1808741](https://github.com/ai4os/ai4-papi/commit/180874145144aec2d839eb6d1182f6597c5addcd))
* start using `prevent_reschedule_on_lost` ([0fb6b5a](https://github.com/ai4os/ai4-papi/commit/0fb6b5af26a9ea46606796e979b81dd9aec268b9))
* start using custom UI for ai4life loader ([081c04a](https://github.com/ai4os/ai4-papi/commit/081c04ac091a78352a2ee82d41bebd9526c48d45))
* start using CVAT images from `ai4oshub` Dockerhub ([4ee60d1](https://github.com/ai4os/ai4-papi/commit/4ee60d1b69fd1609a5d23eb842461fc1e962c804))
* **stats:** keep track of reallocations ([982767d](https://github.com/ai4os/ai4-papi/commit/982767de2ce07adc57a40509a2c34606cc7e9b66))
* support AI4Life models as a tool ([#86](https://github.com/ai4os/ai4-papi/issues/86)) ([9502b50](https://github.com/ai4os/ai4-papi/commit/9502b50bf9b5c55a659d21c05da25b63ca14a323))
* support AI4Life project (`ai4life`) ([#57](https://github.com/ai4os/ai4-papi/issues/57)) ([f183423](https://github.com/ai4os/ai4-papi/commit/f18342304a7ca13db588bd7ae4a006547468c072))
* support deleting CVAT snapshots from storage ([#77](https://github.com/ai4os/ai4-papi/issues/77)) ([695e7a3](https://github.com/ai4os/ai4-papi/commit/695e7a3861aaa1e82961b8e5e6fafcdfe744cd9a))
* support docker tags and custom port in `ai4life` tool ([#90](https://github.com/ai4os/ai4-papi/issues/90)) ([0aee8f3](https://github.com/ai4os/ai4-papi/commit/0aee8f3640094ab8279e8a072faad6549b93175f))
* support making snapshots from deployments ([#62](https://github.com/ai4os/ai4-papi/issues/62)) ([b49d07d](https://github.com/ai4os/ai4-papi/commit/b49d07d9c7caac162d3947288299f6e51da522ec))
* support nomad batch jobs ([#114](https://github.com/ai4os/ai4-papi/issues/114)) ([70c368b](https://github.com/ai4os/ai4-papi/commit/70c368b04fea72e0e308f517b4ed39739a8686fc))
* support Nomad info queries from the provenance pipeline  ([#75](https://github.com/ai4os/ai4-papi/issues/75)) ([a3cf0aa](https://github.com/ai4os/ai4-papi/commit/a3cf0aaf1b6893740823a7d7d7a008c0e89d6cf1))
* support periodic CVAT backups ([#115](https://github.com/ai4os/ai4-papi/issues/115)) ([8b0adda](https://github.com/ai4os/ai4-papi/commit/8b0addaae6f5e6d65fdcfe676a28a695540362e4))
* support refreshing metadata from CI/CD ([#70](https://github.com/ai4os/ai4-papi/issues/70)) ([233b38f](https://github.com/ai4os/ai4-papi/commit/233b38ffc67ba1e2873c072afbd8e11fafe5aa8d))
* support refreshing only the catalog index ([83983e3](https://github.com/ai4os/ai4-papi/commit/83983e39182c49bed578945e117c14a8cf804b9b))
* support try-me endpoints in Nomad ([#59](https://github.com/ai4os/ai4-papi/issues/59)) ([de9b373](https://github.com/ai4os/ai4-papi/commit/de9b3736af5b6098fb7257e0378cb58567e351db))
* update PAPI version in OpenAPI display ([382af38](https://github.com/ai4os/ai4-papi/commit/382af3874b173918bbf478f6f6dd44f5257c4f75))
* update parameters for Fedserver (Differential Privacy) ([#66](https://github.com/ai4os/ai4-papi/issues/66)) ([beec1cb](https://github.com/ai4os/ai4-papi/commit/beec1cbf256bdba62a0850b109143cf6799f5a23))


### Bug Fixes

* adapt MLFLOW_URI depending on VO ([ee39a1b](https://github.com/ai4os/ai4-papi/commit/ee39a1bd70b0c86a90d19e3c042a3876ca6f29c6))
* adapt tryme check to new nomad nomenclature ([230e281](https://github.com/ai4os/ai4-papi/commit/230e281f9a47e2d374838f81957bc46b9c013848))
* add more statuses when allowing purging ([5b4c67f](https://github.com/ai4os/ai4-papi/commit/5b4c67f9a0796bf5d78d723c0a06eadd44a2ffbd))
* allow purging stuck Nomad jobs (`status=queued`) ([21be7a2](https://github.com/ai4os/ai4-papi/commit/21be7a2acb6e01ae9f497f9c41f4dc2933d11d15))
* allow uppercase module names ([f2938c4](https://github.com/ai4os/ai4-papi/commit/f2938c4fbaed4e71952bb32c268b0829ab0175c9))
* always use latest metadata version to validate ([ed2e5bd](https://github.com/ai4os/ai4-papi/commit/ed2e5bdd2e32be56a62232544029c400b176bd15))
* avoid checking for vo membership in `get_user_info` ([9c0243f](https://github.com/ai4os/ai4-papi/commit/9c0243f37a33e137ec327ec3fb98a0bc37cefec9))
* better catch errors in `storage/ls` ([649f284](https://github.com/ai4os/ai4-papi/commit/649f284166cddb45fc1b32ddfe669959885cfb79))
* better catch missing tryme resources ([dda2ff3](https://github.com/ai4os/ai4-papi/commit/dda2ff362e636ac0741722bd61d89bfeb2cfbccb))
* change found status code ([a99746e](https://github.com/ai4os/ai4-papi/commit/a99746e19ce9e2619f8a254b01ab257e235d26ae))
* check vllm is ready before starting LLM tool ([#99](https://github.com/ai4os/ai4-papi/issues/99)) ([56565dc](https://github.com/ai4os/ai4-papi/commit/56565dcdbfc9a98ce8b0fbf3d83e0f3faac00deb))
* correct some references to old federated server ([61204dc](https://github.com/ai4os/ai4-papi/commit/61204dc9846ae7f6900a4d0e402ffc70a2a1831b))
* **deployments:** allow purging jobs with main task `dead` ([765af52](https://github.com/ai4os/ai4-papi/commit/765af527a6c98006e05d1b6ca254dd5932aec66a))
* do not enforce Harbor password when developing ([8060576](https://github.com/ai4os/ai4-papi/commit/806057678c3a6478f46c0e209dab485360671d08))
* fix breaking change in harborapi ([d9c981a](https://github.com/ai4os/ai4-papi/commit/d9c981a03069357349e02d0313911ae958db2613))
* fix CI/CD link for tools ([d6cacfb](https://github.com/ai4os/ai4-papi/commit/d6cacfb8e231fc6ffb15fee1cb08f289c7e37832))
* fix Dockerhub link ([e950cb9](https://github.com/ai4os/ai4-papi/commit/e950cb958e06c86d31223f9b45d62cb7bac7a388))
* fix error retrieving status for queued jobs ([c2a3fc6](https://github.com/ai4os/ai4-papi/commit/c2a3fc6ad8bb1a3a81b6069a2d619a4d3896e4ca))
* fix incorrect task names ([13024bd](https://github.com/ai4os/ai4-papi/commit/13024bd0d32e024b599b330e2adc6a213115d27f))
* fix MinIO info ([02646c4](https://github.com/ai4os/ai4-papi/commit/02646c46d50bfb290883c35a6115ccbca3d39ad6))
* fix refresh ([484fff3](https://github.com/ai4os/ai4-papi/commit/484fff3ec5793323a106a6205c4f5ec57f09adee))
* fix retrieval of Github license ([d8a3161](https://github.com/ai4os/ai4-papi/commit/d8a31614d274f390f829aa9d00147aaea891a355))
* fix storage rm function ([0a524f1](https://github.com/ai4os/ai4-papi/commit/0a524f1154b403c5afa6e7b7a11e9560e1034ea7))
* handle case where user does not belong to VO supported by the project ([d4a2dbc](https://github.com/ai4os/ai4-papi/commit/d4a2dbc47f8bcab751563f9ce63c2ae5f70d4396))
* handle case where user does not belong to VO supported by the project ([4f67727](https://github.com/ai4os/ai4-papi/commit/4f67727e435a4de7120862f72ac40bd2aabb9105))
* improve `get_metadata()` logic ([772dbcd](https://github.com/ai4os/ai4-papi/commit/772dbcd74440a81578c46e6c1d741db3878c5716))
* inference UI should not be killed after 10 mins ([ec41a7e](https://github.com/ai4os/ai4-papi/commit/ec41a7eb89d71fc73ed53d26e56b4a81b6cd1bb9))
* job reschedule on lost ([#123](https://github.com/ai4os/ai4-papi/issues/123)) ([e6ac106](https://github.com/ai4os/ai4-papi/commit/e6ac106d4c981c87c967b6ba53db3821da835e8d))
* **metadata:** update to new Jenkins domain ([16950af](https://github.com/ai4os/ai4-papi/commit/16950afa9446f7f8f77ab7188c99298da6b3b600))
* minor tweaks in minimum resources ([2158aa4](https://github.com/ai4os/ai4-papi/commit/2158aa4f004332a8016fcd30e005a48de8ae6935))
* more accurate deployment status ([5626029](https://github.com/ai4os/ai4-papi/commit/5626029894d57d45eac56c48bad4f3c94dfc45a9))
* obscure rclone password on behalf of users ([3612ae6](https://github.com/ai4os/ai4-papi/commit/3612ae6e6216387dc48571253d9ccdd147ac2887))
* only parse ready nodes ([6e3fa32](https://github.com/ai4os/ai4-papi/commit/6e3fa3200ead028b534ede2a1ad993ad2aa6d413))
* **oscar:** fix deepaas version retrieval ([ba6e0fc](https://github.com/ai4os/ai4-papi/commit/ba6e0fc6de01a601a80ccdddc6a2db6fd4691fba))
* **oscar:** remove typo ([1e03571](https://github.com/ai4os/ai4-papi/commit/1e03571782230f75156be6e16e65560b86f3bc43))
* properly cache `cluster` stats endpoint ([b7f2fb8](https://github.com/ai4os/ai4-papi/commit/b7f2fb85e576c1339e350a55ff78ff2fee8da85d))
* recover zero-reschedule policy ([b11b0c7](https://github.com/ai4os/ai4-papi/commit/b11b0c7137fa5b8d594ede992f85c40a786bc15e))
* remove "hub" from Docker image metadata value ([cb9b07c](https://github.com/ai4os/ai4-papi/commit/cb9b07c174ede8e641daa8804ac54449b688967e))
* remove references to old `deep-oc-generic-dev` ([9129dbd](https://github.com/ai4os/ai4-papi/commit/9129dbdf3083f8d0d7bd8534673101f95fcbf700))
* rename proxies routes ([#128](https://github.com/ai4os/ai4-papi/issues/128)) ([f78ad89](https://github.com/ai4os/ai4-papi/commit/f78ad898bb8b3c3388db6034713e493cb9626db0))
* restore OSCAR service sorting ([4db6659](https://github.com/ai4os/ai4-papi/commit/4db6659af2dd6ad0fa59798530d2ff6394e67371))
* return 404 if directory does not exist ([#100](https://github.com/ai4os/ai4-papi/issues/100)) ([455084b](https://github.com/ai4os/ai4-papi/commit/455084bd89403e7d39b02c510df99006602d8954))
* secrets for each VO should be stored in different paths ([#71](https://github.com/ai4os/ai4-papi/issues/71)) ([111f20f](https://github.com/ai4os/ai4-papi/commit/111f20f0549cddfe0b1166e20965aeef7c8457b5))
* set email task to force pull docker image ([#74](https://github.com/ai4os/ai4-papi/issues/74)) ([2c6f2f8](https://github.com/ai4os/ai4-papi/commit/2c6f2f8416fb6906de1673010b994abed8d686c4))
* snapshot error codes ([#88](https://github.com/ai4os/ai4-papi/issues/88)) ([a867b92](https://github.com/ai4os/ai4-papi/commit/a867b925861e5e521e9bbee7705d8f983356e423))
* stats compute should run on the background ([e9ac53d](https://github.com/ai4os/ai4-papi/commit/e9ac53d3bcf31b3b6bbe303f678cdcd2621605d0))
* **stats:** account for federated cluster migration ([ab1d208](https://github.com/ai4os/ai4-papi/commit/ab1d208aa6807ad4be7553c31e63040e9ca9bd1d))
* **stats:** allow initializing cluster stats when PAPI is used as package ([66898f9](https://github.com/ai4os/ai4-papi/commit/66898f9277cb705bd297b40d8cb485f7ba4e03a9))
* **stats:** fix node status ([0accdb5](https://github.com/ai4os/ai4-papi/commit/0accdb5c8bab64198062506b163f8d1f38018e24))
* **stats:** return reserved disk ([7b2ed94](https://github.com/ai4os/ai4-papi/commit/7b2ed94603b1754a659d08fac806264027239557))
* support OSCAR in ai4life ([f2d026e](https://github.com/ai4os/ai4-papi/commit/f2d026e3dbc109b8c15feeddf9e120a1227bad03))
* update refresh endpoint ([47a6bb5](https://github.com/ai4os/ai4-papi/commit/47a6bb5a9ae5a52413579577596f650239c00ec8))
* upgrade CVAT from `v2.7.3` to `v2.25.0` ([#85](https://github.com/ai4os/ai4-papi/issues/85)) ([7e930e9](https://github.com/ai4os/ai4-papi/commit/7e930e967ae9b67a3bb0e948b395bbda4249fb55))
* upgrade to CVAT v2.28.0 ([#87](https://github.com/ai4os/ai4-papi/issues/87)) ([b8a3582](https://github.com/ai4os/ai4-papi/commit/b8a35827474c68a39d08357286edbe1f4c02e6a6))


### Performance Improvements

* use requests session ([119d28d](https://github.com/ai4os/ai4-papi/commit/119d28dd8e5f68fc3f8c9e1d1af68468f6bf5df9))


### Documentation

* add oidc agent configuration with keycloak ([c793a1b](https://github.com/ai4os/ai4-papi/commit/c793a1baf0d8ccdcde9b529034ac683c20dcb72e))
* change route titles in the Swagger UI ([72fb3d0](https://github.com/ai4os/ai4-papi/commit/72fb3d072790c9dc1ec4f43890fa5b6d97c53c90))
* change route titles in the Swagger UI ([44afe9f](https://github.com/ai4os/ai4-papi/commit/44afe9ff590fda501b9f8b27a275f560288f8d52))
* update README ([94aa9ab](https://github.com/ai4os/ai4-papi/commit/94aa9ab3ec43f192484ad02dbf35cb4d3a467b17))
* update readme ([#122](https://github.com/ai4os/ai4-papi/issues/122)) ([c754a5a](https://github.com/ai4os/ai4-papi/commit/c754a5ace2cc1e139c94f870c725733fac6c4ab6))

## [1.2.0](https://github.com/ai4os/ai4-papi/compare/v1.1.0...v1.2.0) (2024-08-05)


### Features

* add also vscode for old dev-env (backward compatibility) ([4ec9c31](https://github.com/ai4os/ai4-papi/commit/4ec9c31b3d47a424cf3a2e22b78ff61ce84065eb))
* add support for downloading datasets ([#53](https://github.com/ai4os/ai4-papi/issues/53)) ([55f6b77](https://github.com/ai4os/ai4-papi/commit/55f6b77272a9ab6877c834391c702f76aa34c014))
* better tag sorting for dev env ([feb2883](https://github.com/ai4os/ai4-papi/commit/feb28836d2dd9226f91e61a194d698a6432542e4))
* module migration to `ai4os-hub` ([#51](https://github.com/ai4os/ai4-papi/issues/51)) ([87473e0](https://github.com/ai4os/ai4-papi/commit/87473e05055c95f9344907f53ab944ef57050fe7))
* move to federated cluster ([#56](https://github.com/ai4os/ai4-papi/issues/56)) ([6355cd6](https://github.com/ai4os/ai4-papi/commit/6355cd6ca27d99b44519cf9db38622283408f1c0))
* **stats:** account for ineligible nodes ([6560a10](https://github.com/ai4os/ai4-papi/commit/6560a105b62f8e297b4d12ab0f4aed194a48ebb8))
* **stats:** properly aggregate cluster resources ([fd00d14](https://github.com/ai4os/ai4-papi/commit/fd00d1482e676b9f75d5e0c12ea10e4821e54be0))
* update conf for `deep-oc-federated-server` ([#55](https://github.com/ai4os/ai4-papi/issues/55)) ([36082a7](https://github.com/ai4os/ai4-papi/commit/36082a73f5abc5bb2f452760438ded656a509d8c))


### Bug Fixes

* better catch exception ([4133723](https://github.com/ai4os/ai4-papi/commit/4133723d8c42cea649d616499d6db1187254296c))
* reenable dataset checks ([#54](https://github.com/ai4os/ai4-papi/issues/54)) ([af9e8eb](https://github.com/ai4os/ai4-papi/commit/af9e8eb68ebb7942fa97482bac417ab5d0d1d0d4))
* **stats:** account for failing GPU nodes ([93a1608](https://github.com/ai4os/ai4-papi/commit/93a1608c8db877b692f431ec4482de62076fd131))
* **stats:** move stat in to if loop ([c056894](https://github.com/ai4os/ai4-papi/commit/c05689445c5df3a70af52ce037db9fee9f066255))
* temporarily disable DOI checks ([d6a1599](https://github.com/ai4os/ai4-papi/commit/d6a1599a2418b2650e68d38d41a96e0d59ae636d))
* **zenodo:** properly handle `params=None` ([af090c1](https://github.com/ai4os/ai4-papi/commit/af090c1330728d8edb893bbd13a6604e9a56e93e))


### Documentation

* update README ([30133ba](https://github.com/ai4os/ai4-papi/commit/30133ba3940e53a049d703b8563fe21f8fffa815))

## [1.1.0](https://github.com/ai4os/ai4-papi/compare/v1.0.0...v1.1.0) (2024-05-15)


### Features

* add CORS for new endpoints ([9f7ce1f](https://github.com/ai4os/ai4-papi/commit/9f7ce1f86b870ce1a7d311b843461c3636c8d3b4))
* add support for Vault secrets ([#44](https://github.com/ai4os/ai4-papi/issues/44)) ([11116ec](https://github.com/ai4os/ai4-papi/commit/11116eca84dafedcdf370b449b0e078437929442))


### Bug Fixes

* force pulling of Docker images ([c811bba](https://github.com/ai4os/ai4-papi/commit/c811bba6e6412d547d2ee1f029348958dddaa2c7))
* only retrieve GPU models from _eligible_ nodes ([3733159](https://github.com/ai4os/ai4-papi/commit/3733159f8362bccb4ada23630b37c5ad8818df2a))
* properly monkey-patch `Catalog` class using `MethodType` ([ce8156b](https://github.com/ai4os/ai4-papi/commit/ce8156b01df937bdf51f20a3b0d2ef9ac26ed504))
* set license year/owner ([ecbcde7](https://github.com/ai4os/ai4-papi/commit/ecbcde7512c357e79981fa87ad16b9ce7b90cee5))
* set max RAM memory ([39a1384](https://github.com/ai4os/ai4-papi/commit/39a13844a631a1313941decb68fb3c758f38c812))

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
