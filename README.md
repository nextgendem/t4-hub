# T4-Hub (O2auth and intermediate)
Sessions division with Google Authentication and redirection with Python Container (Flask)
> [!IMPORTANT]  
> Until further notice, in step 1 replace `src/common/install/kasm_vnc/www/package.json` with
  `/fix/package.json` folder when building locally https://github.com/OpenDx28/docker-vnc-base
<details>
 <summary>Preinstallation:</summary>
 
1. Follow https://github.com/nextgendem/t4-novnc steps.
  
2. Clone oauth2 repository.
```bash
git clone  https://github.com/oauth2-proxy/oauth2-proxy
```
3. Build repository with <code style="color : red">o2-auth/o2-auth</code> tag
```bash
cd oauth2-proxy
docker build -t o2-auth/o2-auth .
```
</details>
<details>
 <summary>Configurations:</summary>

- /data/user_ports : Contains auth user and port assignated (at the moment there are only 2 deployed)
 
</details>
Run the docker

```bash
docker-compose up
```

## Done 
<details>
 <summary>10/10</summary
                
 - Prototype runnable
</details>


## Pending
<details>
 <summary>10/10 Pending ideas</summary>
 
 - Internal port assignation (Nginx)
 - EasyDav easy vinculation (in-page or in-vnc)
 - Kubernetes or other spawning Docker option to automatic Container creator 
</details>
