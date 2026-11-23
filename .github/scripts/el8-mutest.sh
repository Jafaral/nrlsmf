#!/bin/sh
# Build nrlsmf and run the mutests inside a Rocky Linux 8 VM.
# Runs as root in the VM; $1 is the source tree copied in by the runner.
set -eux
src=${1:-/root/nrlsmf}

# NetworkManager treats localhost and localhost.localdomain as unset and
# replaces them. The reverse lookup of the QEMU address 10.0.2.15 comes
# back as ip-10-0-2-15.ec2.internal, and munet aborts if that happens
# after it has recorded the hostname. A real static name is left alone.
# SELinux in the cloud image denies zebra the bind-mounted test configs.
if command -v cloud-init >/dev/null 2>&1; then
    cloud-init status --wait || true
fi
pin_hostname() {
    if command -v hostnamectl >/dev/null 2>&1; then
        hostnamectl set-hostname el8
    else
        printf '%s\n' el8 > /etc/hostname
        hostname el8
    fi
}
pin_hostname
mkdir -p /etc/NetworkManager/conf.d
printf '%s\n' '[main]' 'hostname-mode=none' \
    > /etc/NetworkManager/conf.d/90-hostname.conf
nmcli general reload || true
if [ -w /sys/fs/selinux/enforce ]; then
    echo 0 > /sys/fs/selinux/enforce
fi

dnf -y install dnf-plugins-core epel-release
dnf config-manager --set-enabled powertools devel
dnf -y install git gcc-c++ make libnetfilter_queue-devel \
    iproute iproute-tc iputils ethtool tcpdump iperf tmux procps-ng \
    python3.11 python3.11-pip
# AppStream frr is 7.5. Its nhrpd accepts the NHS command and then
# leaves the protocol address unset, so spokes never register.
# https://rpm.frrouting.org/
curl -fsSL -O https://rpm.frrouting.org/repo/frr-stable-repo.el8.noarch.rpm
dnf -y install ./frr-stable-repo.el8.noarch.rpm
dnf -y install frr frr-pythontools
rm -f frr-stable-repo.el8.noarch.rpm
# ip_gre lives in kernel-modules-extra on some EL8 kernels.
dnf -y install "kernel-modules-extra-$(uname -r)" || true
modprobe ip_gre
# The tests start FRR from /usr/lib/frr. A distro package may still
# install the scripts under /usr/libexec/frr.
if [ ! -x /usr/lib/frr/frrinit.sh ] && [ -x /usr/libexec/frr/frrinit.sh ]; then
    ln -s /usr/libexec/frr /usr/lib/frr
fi
uname -a

python3.11 -m venv /opt/munet
/opt/munet/bin/pip install --upgrade pip
/opt/munet/bin/pip install munet

cd "$src"
make -j"$(nproc)"
make install

rm -rf /tmp/mutest
mkdir -p /tmp/mutest
cd tests/mutests
pin_hostname
PATH="/opt/munet/bin:$PATH" mutest
