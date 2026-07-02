# Maintainer: qwqgong-ui <qwqgong-ui@users.noreply.github.com>

pkgname=omen-8a4d-user-scheduler
pkgver=0.1.0
pkgrel=1
pkgdesc='暗影精灵8A4D用户调度器'
arch=('any')
url='https://github.com/qwqgong-ui/omen-8a4d-user-scheduler'
license=('MIT')
depends=('python')
source=("${pkgname}-${pkgver}.tar.gz")
sha256sums=('9e277539815d943d45a0d8714ca7b93f2bbae4def5d91ff44b75416b579466f7')

package() {
  cd "${srcdir}/${pkgname}-${pkgver}"

  local site_packages
  site_packages="$(python - <<'PY'
import sysconfig
print(sysconfig.get_paths()["purelib"].lstrip("/"))
PY
)"

  install -dm755 "${pkgdir}/${site_packages}"
  cp -a src/omen_8a4d_user_scheduler "${pkgdir}/${site_packages}/"

  install -Dm755 bin/omen-8a4d-user-scheduler \
    "${pkgdir}/usr/bin/omen-8a4d-user-scheduler"
  install -Dm644 systemd/omen-8a4d-user-scheduler.service \
    "${pkgdir}/usr/lib/systemd/system/omen-8a4d-user-scheduler.service"
  install -Dm644 LICENSE \
    "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
  install -Dm644 README_user_scheduler.md \
    "${pkgdir}/usr/share/doc/${pkgname}/README_user_scheduler.md"
  install -Dm644 docs/hp-wmi-ir.md \
    "${pkgdir}/usr/share/doc/${pkgname}/hp-wmi-ir.md"
}
