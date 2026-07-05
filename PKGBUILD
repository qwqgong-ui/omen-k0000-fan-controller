# Maintainer: qwqgong-ui <qwqgong-ui@users.noreply.github.com>

pkgname=omen-k0000-fan-controller
pkgver=0.1.0
pkgrel=2
pkgdesc='omen-k0000-fan-controller'
arch=('any')
url='https://github.com/qwqgong-ui/omen-k0000-fan-controller'
license=('MIT')
depends=('python')
conflicts=('omen-8a4d-user-scheduler')
replaces=('omen-8a4d-user-scheduler')
source=("${pkgname}-${pkgver}.tar.gz")
sha256sums=('13f03ec7f228bd103bb9500eb9a141cb5a7e9133a54c412c14e3d684d64d513e')

package() {
  cd "${srcdir}/${pkgname}-${pkgver}"

  local site_packages
  site_packages="$(python - <<'PY'
import sysconfig
print(sysconfig.get_paths()["purelib"].lstrip("/"))
PY
)"

  install -dm755 "${pkgdir}/${site_packages}"
  cp -a src/omen_k0000_fan_controller "${pkgdir}/${site_packages}/"

  install -Dm755 bin/omen-k0000-fan-controller \
    "${pkgdir}/usr/bin/omen-k0000-fan-controller"
  install -Dm644 systemd/omen-k0000-fan-controller.service \
    "${pkgdir}/usr/lib/systemd/system/omen-k0000-fan-controller.service"
  install -Dm644 LICENSE \
    "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
  install -Dm644 README_user_scheduler.md \
    "${pkgdir}/usr/share/doc/${pkgname}/README_user_scheduler.md"
  install -Dm644 docs/hp-wmi-ir.md \
    "${pkgdir}/usr/share/doc/${pkgname}/hp-wmi-ir.md"
}
